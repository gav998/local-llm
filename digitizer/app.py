#!/usr/bin/env python3
"""Portable loopback-only semantic PDF digitizer backed by PaddleOCR."""

from __future__ import annotations

import argparse
import base64
import copy
import html
import json
import locale
import math
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import unquote, urlparse

import yaml

PDF_SUFFIX = ".pdf"
MAX_PDF_BYTES = 1 << 30
SCHEMA_VERSION = 2
OCR_MODES = {"text", "seal", "formula", "text_seal", "text_formula", "none"}
ORIENTATIONS = {0, 90, 180, 270}


def load_digitizer_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or Path(__file__).with_name("presets.yaml")
    with config_path.open("r", encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if not isinstance(value, dict):
        raise RuntimeError(f"Конфигурация {config_path} должна содержать YAML-объект")
    default_prompt = str(value.get("default_prompt") or "").strip()
    presets = value.get("presets")
    if not default_prompt or not isinstance(presets, list) or not presets:
        raise RuntimeError(
            f"В {config_path} обязательны default_prompt и непустой список presets"
        )
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_preset in presets:
        if not isinstance(raw_preset, dict):
            raise RuntimeError("Каждый preset в YAML должен быть объектом")
        preset = copy.deepcopy(raw_preset)
        preset_id = str(preset.get("id") or "").strip()
        if not preset_id or preset_id in seen:
            raise RuntimeError("Каждому preset в YAML нужен уникальный id")
        seen.add(preset_id)
        preset["id"] = preset_id
        preset["name"] = str(preset.get("name") or preset_id)
        preset["description"] = str(preset.get("description") or "")
        preset["type"] = str(preset.get("type") or "record")
        fields: list[dict[str, str]] = []
        for raw_field in preset.get("fields") or []:
            if not isinstance(raw_field, dict):
                raise RuntimeError(f"Поля preset {preset_id} должны быть YAML-объектами")
            field = {
                "key": str(raw_field.get("key") or "").strip(),
                "name": str(raw_field.get("name") or "").strip(),
                "ocrMode": str(
                    raw_field.get("ocr_mode")
                    or raw_field.get("ocrMode")
                    or "text"
                ),
                "aiPrompt": str(raw_field.get("prompt") or "").strip(),
            }
            if not field["key"] or not field["name"] or not field["aiPrompt"]:
                raise RuntimeError(
                    f"Каждому полю preset {preset_id} нужны key, name и prompt"
                )
            if field["ocrMode"] not in OCR_MODES:
                raise RuntimeError(f"Неизвестный OCR-режим у {preset_id}.{field['key']}")
            fields.append(field)
        preset["fields"] = fields
        preset["dynamicFieldPrompt"] = str(
            preset.get("dynamic_field_prompt") or default_prompt
        ).strip()
        preset["cellPrompt"] = str(
            preset.get("cell_prompt") or preset["dynamicFieldPrompt"]
        ).strip()
        normalized.append(preset)
    return {"defaultPrompt": default_prompt, "presets": normalized}


DIGITIZER_CONFIG = load_digitizer_config()
DEFAULT_PROMPT = DIGITIZER_CONFIG["defaultPrompt"]
PRESETS: list[dict[str, Any]] = DIGITIZER_CONFIG["presets"]


def import_pymupdf():
    try:
        import pymupdf

        return pymupdf
    except ImportError:
        import fitz

        return fitz


def uid() -> str:
    return uuid.uuid4().hex


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def clean_text(value: str) -> str:
    value = re.sub(r"```(?:text)?\s*|```", "", value, flags=re.I)
    return "\n".join(line.rstrip() for line in value.strip().splitlines()).strip()


def clean_ocr_markup(value: str) -> str:
    """Keep OCR text while dropping image placeholders emitted by PP-StructureV3."""
    value = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", value, flags=re.S)
    value = re.sub(r"<img\b[^>]*>", "", value, flags=re.I | re.S)
    value = re.sub(r"</?(?:div|p|span)\b[^>]*>", "", value, flags=re.I | re.S)
    # PP-StructureV3 emits short detected fragments as Markdown headings.  A
    # digitizer field is plain text, so keeping the leading "# " only adds
    # OCR noise to numbers, names and other values.
    value = re.sub(r"(?m)^\s{0,3}#{1,6}[ \t]+", "", value)
    return clean_text(html.unescape(value))


def combine_ocr_outputs(sources: list[dict[str, Any]]) -> str:
    """Combine several selections belonging to one semantic value."""
    values = [
        str(item.get("output") or "").strip()
        for item in sources
        if item.get("status") in {"recognized", "modified"} and item.get("output")
    ]
    return " ".join(values)


def _ocr_recognized_texts(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    texts = value.get("rec_texts") or value.get("recTexts") or []
    if not isinstance(texts, list):
        return []
    return [clean_ocr_markup(str(item)) for item in texts if clean_ocr_markup(str(item))]


def flatten_ocr_result(value: str, mode: str = "text") -> str:
    wanted_labels = set()
    if "seal" in mode:
        wanted_labels.add("seal")
    if "formula" in mode:
        wanted_labels.update({"formula", "formula_number"})
    general: list[str] = []
    selected: list[str] = []
    fallback: list[str] = []
    markdown_parts: list[str] = []
    for line in value.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        result = payload.get("result") or {}
        markdown = (result.get("markdown") or {}).get("text")
        if isinstance(markdown, list):
            markdown = "\n".join(str(item) for item in markdown)
        if isinstance(markdown, str) and markdown.strip():
            cleaned_markdown = clean_ocr_markup(markdown)
            if cleaned_markdown:
                markdown_parts.append(cleaned_markdown)
        for layout in result.get("layoutParsingResults") or []:
            pruned = layout.get("prunedResult") or {}
            general.extend(_ocr_recognized_texts(pruned.get("overall_ocr_res")))
            blocks = pruned.get("parsing_res_list") or []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                content = block.get("block_content")
                if not content:
                    continue
                text = clean_ocr_markup(str(content))
                if not text:
                    continue
                label = str(block.get("block_label") or "").casefold()
                if not any(token in label for token in ("image", "figure", "chart")):
                    fallback.append(text)
                if wanted_labels and any(token == label or token in label for token in wanted_labels):
                    selected.append(text)
    if mode == "text":
        parts = general or fallback or markdown_parts
    elif mode in {"text_seal", "text_formula"}:
        parts = general + [item for item in selected if item not in general]
        parts = parts or fallback or markdown_parts
    else:
        parts = selected or general or fallback or markdown_parts
    if not parts:
        raise RuntimeError("PaddleOCR вернул пустой результат")
    return clean_text("\n".join(parts))


def normalized_cuts(values: Any) -> list[float]:
    cuts = [0.0, 1.0]
    if isinstance(values, list):
        for value in values:
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number) and 0.0 < number < 1.0:
                cuts.append(round(number, 6))
    return sorted(set(cuts))


def validate_rect(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError("У области отсутствует прямоугольник")
    try:
        rect = {key: float(value.get(key, -1)) for key in ("x", "y", "w", "h")}
    except (TypeError, ValueError) as exc:
        raise ValueError("Координаты области повреждены; выделите область повторно") from exc
    if (
        not all(math.isfinite(number) for number in rect.values())
        or rect["x"] < 0
        or rect["y"] < 0
        or rect["w"] <= 0
        or rect["h"] <= 0
        or rect["x"] + rect["w"] > 1.000001
        or rect["y"] + rect["h"] > 1.000001
    ):
        raise ValueError("Координаты области должны находиться внутри страницы")
    return rect


def normalize_orientation(value: Any) -> int:
    try:
        orientation = int(value or 0) % 360
    except (TypeError, ValueError):
        orientation = 0
    if orientation not in ORIENTATIONS:
        raise ValueError("Ориентация должна быть 0, 90, 180 или 270 градусов")
    return orientation


def sidecar_path(source: Path) -> Path:
    return source.with_name(source.name + ".digitizer.json")


def template_path(source: Path) -> Path:
    return source.with_name("digitizer.templates.json")


def new_source(page: int, rect: dict[str, float], kind: str = "region") -> dict[str, Any]:
    return {
        "id": uid(),
        "kind": kind,
        "page": page,
        "rect": validate_rect(rect),
        "orientation": 0,
        "properties": {"useLlm": False},
        "status": "pending",
        "output": None,
        "error": None,
    }


def new_field(
    key: str,
    name: str,
    ocr_mode: str = "text",
    ai_prompt: str = "",
) -> dict[str, Any]:
    return {
        "id": uid(),
        "key": key,
        "name": name,
        "ocrMode": ocr_mode if ocr_mode in OCR_MODES else "text",
        "aiPrompt": ai_prompt.strip() or DEFAULT_PROMPT,
        "orientation": 0,
        "value": "",
        "sources": [],
    }


def preset_catalog() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for preset in PRESETS:
        result.append(
            {
                "id": preset["id"],
                "name": preset["name"],
                "description": preset["description"],
                "type": preset.get("type", "record"),
                "dynamicFieldPrompt": preset.get("dynamicFieldPrompt", DEFAULT_PROMPT),
                "cellPrompt": preset.get("cellPrompt", DEFAULT_PROMPT),
                "fields": copy.deepcopy(preset.get("fields", [])),
            }
        )
    return result


def create_object(preset_id: str, name: str | None = None) -> dict[str, Any]:
    preset = next((item for item in PRESETS if item["id"] == preset_id), None)
    if preset is None:
        raise ValueError("Неизвестный тип объекта")
    object_type = preset.get("type", "record")
    obj: dict[str, Any] = {
        "id": uid(),
        "type": object_type,
        "preset": preset_id,
        "name": (name or preset["name"]).strip(),
        "key": preset_id,
        "orientation": 0,
    }
    if object_type == "record":
        obj["fields"] = [
            new_field(
                field["key"], field["name"], field["ocrMode"], field["aiPrompt"]
            )
            for field in preset.get("fields", [])
        ]
    else:
        obj.update(
            {
                "title": new_field(
                    "title",
                    "Название таблицы",
                    "text",
                    str(preset.get("dynamicFieldPrompt") or DEFAULT_PROMPT),
                ),
                "columns": [],
                "rows": [],
                "blocks": [],
                "groups": [],
            }
        )
    return obj


def new_document(source: Path, page_count: int) -> dict[str, Any]:
    now = time.time()
    return {
        "schema": SCHEMA_VERSION,
        "source": str(source),
        "pageCount": page_count,
        "templateId": None,
        "objects": [],
        "data": {},
        "created": now,
        "updated": now,
    }


def unique_key(base: str, used: set[str]) -> str:
    key = re.sub(r"[^\w-]+", "_", base.strip().casefold(), flags=re.UNICODE).strip("_")
    key = key or "value"
    candidate = key
    number = 2
    while candidate in used:
        candidate = f"{key}_{number}"
        number += 1
    used.add(candidate)
    return candidate


def migrate_v1(document: dict[str, Any]) -> dict[str, Any]:
    migrated = copy.deepcopy(document)
    migrated["schema"] = SCHEMA_VERSION
    objects: list[dict[str, Any]] = []
    for legacy in migrated.get("objects") or []:
        name = str(legacy.get("name") or legacy.get("key") or "Объект")
        key = str(legacy.get("key") or name)
        if legacy.get("type") == "field":
            field = new_field("value", "Значение", "text")
            field["value"] = str((legacy.get("result") or {}).get("value") or "")
            field["sources"] = []
            for region in legacy.get("regions") or []:
                source = copy.deepcopy(region)
                source["kind"] = "region"
                source["orientation"] = normalize_orientation(
                    (source.get("properties") or {}).get("orientation", 0)
                )
                field["sources"].append(source)
            objects.append(
                {
                    "id": legacy.get("id") or uid(),
                    "type": "record",
                    "preset": "custom_field",
                    "name": name,
                    "key": key,
                    "orientation": 0,
                    "fields": [field],
                }
            )
            continue
        result = legacy.get("result") or {}
        table: dict[str, Any] = {
            "id": legacy.get("id") or uid(),
            "type": "table",
            "preset": "table",
            "name": name,
            "key": key,
            "orientation": 0,
            "title": new_field("title", "Название таблицы", "text"),
            "columns": [],
            "rows": [],
            "blocks": [],
            "groups": [],
        }
        used: set[str] = set()
        headers = [str(item) for item in result.get("columns") or []]
        rows = [list(item) for item in result.get("rows") or [] if isinstance(item, list)]
        width = max([len(headers), *(len(row) for row in rows)], default=0)
        for index in range(width):
            title = headers[index] if index < len(headers) else f"Столбец {index + 1}"
            column = new_field(unique_key(title, used), title, "text")
            column["value"] = title
            table["columns"].append(column)
        for legacy_row in rows:
            row_id = uid()
            cells = []
            for index, column in enumerate(table["columns"]):
                cells.append(
                    {
                        "id": uid(),
                        "columnId": column["id"],
                        "value": str(legacy_row[index]) if index < len(legacy_row) else "",
                        "orientation": None,
                    }
                )
            table["rows"].append({"id": row_id, "blockId": None, "cells": cells})
        for region in legacy.get("regions") or []:
            kind = region.get("kind")
            if kind == "table_rows":
                source = copy.deepcopy(region)
                source["kind"] = "table_block"
                source["orientation"] = 0
                table["blocks"].append(
                    {
                        "id": uid(),
                        "name": f"Блок строк {len(table['blocks']) + 1}",
                        "region": source,
                        "grid": copy.deepcopy(region.get("grid") or {"columns": [], "rows": []}),
                        "rowIds": [],
                    }
                )
            elif kind == "group_value":
                group = new_field(
                    unique_key(str((region.get("properties") or {}).get("column") or "Группа"), used),
                    str((region.get("properties") or {}).get("column") or "Группа"),
                    "text",
                )
                group["value"] = str(region.get("output") or "")
                group["rowIds"] = []
                source = copy.deepcopy(region)
                source["kind"] = "region"
                source["orientation"] = 0
                group["sources"] = [source]
                table["groups"].append(group)
        objects.append(table)
    migrated["objects"] = objects
    migrated["migratedFromSchema"] = 1
    return migrated


def normalize_source(source: dict[str, Any]) -> None:
    source.setdefault("id", uid())
    source["rect"] = validate_rect(source.get("rect"))
    source["page"] = int(source.get("page", 0))
    source["orientation"] = normalize_orientation(source.get("orientation", 0))
    source.setdefault("properties", {"useLlm": False, "prompt": ""})
    source.setdefault("status", "pending")
    source.setdefault("output", None)
    source.setdefault("error", None)


def normalize_field(field: dict[str, Any], default_prompt: str | None = None) -> None:
    field.setdefault("id", uid())
    field["name"] = str(field.get("name") or "Поле")
    field["key"] = str(field.get("key") or "value")
    mode = str(field.get("ocrMode") or "text")
    field["ocrMode"] = mode if mode in OCR_MODES else "text"
    field["aiPrompt"] = str(
        default_prompt if default_prompt is not None else field.get("aiPrompt") or DEFAULT_PROMPT
    ).strip()
    field["orientation"] = normalize_orientation(field.get("orientation", 0))
    field["value"] = str(field.get("value") or "")
    field.setdefault("sources", [])
    for source in field["sources"]:
        normalize_source(source)


def normalize_document(document: dict[str, Any]) -> dict[str, Any]:
    if document.get("schema", 1) == 1:
        document = migrate_v1(document)
    if document.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"Поддерживается схема документа {SCHEMA_VERSION}")
    document.setdefault("objects", [])
    for obj in document["objects"]:
        obj.setdefault("id", uid())
        obj["name"] = str(obj.get("name") or "Объект")
        obj["key"] = str(obj.get("key") or obj["name"])
        obj["orientation"] = normalize_orientation(obj.get("orientation", 0))
        preset = next(
            (item for item in PRESETS if item["id"] == obj.get("preset")), None
        )
        configured_fields = {
            item["key"]: item for item in (preset or {}).get("fields", [])
        }
        dynamic_prompt = str(
            (preset or {}).get("dynamicFieldPrompt") or DEFAULT_PROMPT
        )
        if obj.get("type") == "record":
            obj.setdefault("fields", [])
            for field in obj["fields"]:
                configured = configured_fields.get(str(field.get("key") or "")) or {}
                normalize_field(field, str(configured.get("aiPrompt") or dynamic_prompt))
        elif obj.get("type") == "table":
            title = obj.setdefault(
                "title", new_field("title", "Название таблицы", "text", dynamic_prompt)
            )
            normalize_field(title, dynamic_prompt)
            for name in ("columns", "groups"):
                obj.setdefault(name, [])
                for field in obj[name]:
                    normalize_field(field, dynamic_prompt)
                    if name == "columns":
                        field["cellPrompt"] = str(
                            (preset or {}).get("cellPrompt")
                            or field.get("cellPrompt")
                            or dynamic_prompt
                        ).strip()
            obj.setdefault("rows", [])
            obj.setdefault("blocks", [])
            for block in obj["blocks"]:
                block.setdefault("id", uid())
                block.setdefault("name", "Блок строк")
                block.setdefault("grid", {"columns": [], "rows": []})
                block.setdefault("rowIds", [])
                normalize_source(block["region"])
                block["region"]["kind"] = "table_block"
            for row in obj["rows"]:
                row.setdefault("id", uid())
                row.setdefault("blockId", None)
                row.setdefault("cells", [])
                for cell in row["cells"]:
                    cell.setdefault("id", uid())
                    cell["value"] = str(cell.get("value") or "")
                    cell.setdefault("sources", [])
                    for source in cell["sources"]:
                        normalize_source(source)
                    if cell.get("orientation") is not None:
                        cell["orientation"] = normalize_orientation(cell["orientation"])
            for group in obj["groups"]:
                group.setdefault("rowIds", [])
        else:
            raise ValueError("Неизвестный тип объекта")
    return document


def materialize(document: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for obj in document.get("objects") or []:
        key = str(obj.get("key") or obj.get("name") or obj.get("id"))
        if obj.get("type") == "record":
            fields = obj.get("fields") or []
            values = {str(field.get("key")): str(field.get("value") or "") for field in fields}
            if obj.get("preset") == "custom_field" and len(fields) == 1:
                data[key] = next(iter(values.values()), "")
            else:
                data[key] = values
            continue
        columns = obj.get("columns") or []
        groups = obj.get("groups") or []
        names = [str(group.get("name")) for group in groups] + [
            str(column.get("value") or column.get("name")) for column in columns
        ]
        rows: list[dict[str, str]] = []
        for row in obj.get("rows") or []:
            row_id = row.get("id")
            values: dict[str, str] = {}
            for group in groups:
                values[str(group.get("name"))] = (
                    str(group.get("value") or "") if row_id in group.get("rowIds", []) else ""
                )
            cells = {cell.get("columnId"): cell for cell in row.get("cells") or []}
            for column in columns:
                cell = cells.get(column.get("id")) or {}
                values[str(column.get("value") or column.get("name"))] = str(
                    cell.get("value") or ""
                )
            rows.append(values)
        data[key] = {
            "title": str((obj.get("title") or {}).get("value") or ""),
            "columns": names,
            "rows": rows,
        }
    document["data"] = data
    document["updated"] = time.time()
    return document


def iter_regions(
    document: dict[str, Any],
) -> Iterator[tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]]:
    for obj in document.get("objects") or []:
        if obj.get("type") == "record":
            for field in obj.get("fields") or []:
                for source in field.get("sources") or []:
                    yield obj, field, source, "field"
        else:
            title = obj.get("title") or {}
            for source in title.get("sources") or []:
                yield obj, title, source, "title"
            for column in obj.get("columns") or []:
                for source in column.get("sources") or []:
                    yield obj, column, source, "column"
            for group in obj.get("groups") or []:
                for source in group.get("sources") or []:
                    yield obj, group, source, "group"
            for row in obj.get("rows") or []:
                for cell in row.get("cells") or []:
                    for source in cell.get("sources") or []:
                        yield obj, cell, source, "cell"
            for block in obj.get("blocks") or []:
                yield obj, block, block["region"], "table_block"


class SourceRegistry:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root
        self.workspace: Path | None = None
        self.index_path = data_root / "remote-sources.json"
        self._lock = threading.Lock()
        data_root.mkdir(parents=True, exist_ok=True)
        try:
            self.remote_index = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.remote_index: dict[str, str] = {}

    def set_workspace(self, value: str) -> Path:
        root = Path(value).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Рабочий путь не является каталогом")
        self.workspace = root
        return root

    def resolve(self, value: str) -> Path:
        value = unquote(value.strip())
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"}:
            return self._download(value)
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            if self.workspace is None:
                raise ValueError("Для относительного пути сначала выберите каталог")
            candidate = self.workspace / candidate
        source = candidate.resolve(strict=True)
        if not source.is_file() or source.suffix.lower() != PDF_SUFFIX:
            raise ValueError("Источник должен быть существующим PDF-файлом")
        return source

    def _download(self, url: str) -> Path:
        import httpx

        with self._lock:
            relative = self.remote_index.get(url)
            if relative:
                existing = (self.data_root / relative).resolve()
                if existing.is_file():
                    return existing
            bucket = self.data_root / "blob-cache" / uid()
            bucket.mkdir(parents=True)
            name = Path(urlparse(url).path).name or "document.pdf"
            if not name.lower().endswith(PDF_SUFFIX):
                name += PDF_SUFFIX
            destination = bucket / name
            total = 0
            with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as response:
                response.raise_for_status()
                with destination.open("wb") as output:
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > MAX_PDF_BYTES:
                            raise ValueError("PDF из blob-хранилища превышает 1 ГБ")
                        output.write(chunk)
            with destination.open("rb") as downloaded:
                if downloaded.read(5) != b"%PDF-":
                    destination.unlink(missing_ok=True)
                    raise ValueError("Blob-хранилище вернуло не PDF")
            self.remote_index[url] = destination.relative_to(self.data_root).as_posix()
            atomic_json(self.index_path, self.remote_index)
            return destination


class DocumentStore:
    def __init__(self) -> None:
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()

    def lock(self, source: Path) -> threading.RLock:
        key = str(source).casefold() if os.name == "nt" else str(source)
        with self._guard:
            return self._locks.setdefault(key, threading.RLock())

    def load(self, source: Path) -> dict[str, Any]:
        with self.lock(source):
            pymupdf = import_pymupdf()
            with pymupdf.open(source) as pdf:
                page_count = pdf.page_count
            try:
                document = json.loads(sidecar_path(source).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                document = new_document(source, page_count)
            document = normalize_document(document)
            document["source"] = str(source)
            document["pageCount"] = page_count
            return materialize(document)

    def save(self, source: Path, document: dict[str, Any]) -> dict[str, Any]:
        with self.lock(source):
            document = normalize_document(copy.deepcopy(document))
            document["source"] = str(source)
            normalized = materialize(document)
            atomic_json(sidecar_path(source), normalized)
            return normalized

    def mutate(
        self, source: Path, callback: Callable[[dict[str, Any]], None]
    ) -> dict[str, Any]:
        with self.lock(source):
            document = self.load(source)
            callback(document)
            return self.save(source, document)


class PaddleClient:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token

    def health(self) -> dict[str, Any]:
        import httpx

        try:
            response = httpx.get(f"{self.url}/health", timeout=5.0)
            response.raise_for_status()
            payload = response.json()
            payload.setdefault("capabilities", ["text", "table", "seal", "formula"])
            return payload
        except Exception as exc:
            return {"status": "unavailable", "error": str(exc), "capabilities": []}

    def recognize(self, image: bytes, mode: str = "text") -> str:
        import httpx

        if mode not in OCR_MODES or mode == "none":
            raise ValueError("Для области не выбран поддерживаемый OCR-режим")
        options = {
            "useTableRecognition": False,
            "useSealRecognition": mode in {"seal", "text_seal"},
            "useFormulaRecognition": mode in {"formula", "text_formula"},
            "useRegionDetection": False,
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useTextlineOrientation": False,
            "formatBlockContent": True,
        }
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(timeout=httpx.Timeout(120.0, read=600.0)) as client:
            response = client.post(
                f"{self.url}/api/v2/ocr/jobs",
                headers=headers,
                data={
                    "model": "PP-StructureV3",
                    "optionalPayload": json.dumps(options),
                },
                files={"file": ("region.png", image, "image/png")},
            )
            response.raise_for_status()
            job_id = response.json()["data"]["jobId"]
            deadline = time.monotonic() + 60 * 60
            while time.monotonic() < deadline:
                status = client.get(f"{self.url}/api/v2/ocr/jobs/{job_id}", headers=headers)
                status.raise_for_status()
                payload = status.json()["data"]
                if payload["state"] == "done":
                    result = client.get(
                        f"{self.url}/api/v2/ocr/jobs/{job_id}/result", headers=headers
                    )
                    result.raise_for_status()
                    return flatten_ocr_result(result.text, mode)
                if payload["state"] == "failed":
                    raise RuntimeError(payload.get("errorMsg") or "Ошибка PaddleOCR")
                time.sleep(0.5)
        raise TimeoutError("PaddleOCR не завершил область за один час")


class LlamaClient:
    def __init__(self, url: str, api_key: str = "") -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key

    def process(self, text: str, prompt: str) -> str:
        import httpx

        if not self.url:
            raise RuntimeError(
                "llama.cpp не настроена; запустите её или отключите исправление через LLM"
            )
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        with httpx.Client(timeout=httpx.Timeout(30.0, read=600.0), headers=headers) as client:
            models = client.get(f"{self.url}/models")
            models.raise_for_status()
            model = models.json().get("data", [{}])[0].get("id", "local-model")
            response = client.post(
                f"{self.url}/chat/completions",
                json={
                    "model": model,
                    "temperature": 0,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Ты исправляешь OCR. Ответ должен содержать только итоговый текст.",
                        },
                        {"role": "user", "content": f"{prompt}\n\nOCR:\n{text}"},
                    ],
                },
            )
            response.raise_for_status()
            return clean_text(response.json()["choices"][0]["message"]["content"])


def render_crop(
    source: Path, page_number: int, rect: dict[str, float], orientation: int = 0
) -> bytes:
    pymupdf = import_pymupdf()
    with pymupdf.open(source) as pdf:
        if page_number < 1 or page_number > pdf.page_count:
            raise ValueError("Страница области не существует")
        page = pdf.load_page(page_number - 1)
        bounds = page.rect
        clip = pymupdf.Rect(
            bounds.x0 + rect["x"] * bounds.width,
            bounds.y0 + rect["y"] * bounds.height,
            bounds.x0 + (rect["x"] + rect["w"]) * bounds.width,
            bounds.y0 + (rect["y"] + rect["h"]) * bounds.height,
        )
        matrix = pymupdf.Matrix(2.5, 2.5)
        if orientation:
            matrix = matrix.prerotate(orientation)
        pixmap = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
        return pixmap.tobytes("png")


def cell_rect(
    rect: dict[str, float], x0: float, x1: float, y0: float, y1: float
) -> dict[str, float]:
    return {
        "x": rect["x"] + x0 * rect["w"],
        "y": rect["y"] + y0 * rect["h"],
        "w": (x1 - x0) * rect["w"],
        "h": (y1 - y0) * rect["h"],
    }


class ExtractionTasks:
    def __init__(self, store: DocumentStore, paddle: PaddleClient, llama: LlamaClient):
        self.store = store
        self.paddle = paddle
        self.llama = llama
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def submit(self, source: Path, region_id: str) -> str:
        task_id = uid()
        with self._lock:
            self._tasks[task_id] = {"state": "queued", "regionId": region_id}
        self._set_region(source, region_id, status="queued", error=None)
        threading.Thread(
            target=self._run,
            args=(task_id, source, region_id),
            daemon=True,
            name=f"digitizer-{task_id[:8]}",
        ).start()
        return task_id

    def submit_correction(self, source: Path, target_id: str) -> str:
        task_id = uid()
        with self._lock:
            self._tasks[task_id] = {
                "state": "queued",
                "type": "ai-correction",
                "targetId": target_id,
            }
        threading.Thread(
            target=self._run_correction,
            args=(task_id, source, target_id),
            daemon=True,
            name=f"digitizer-ai-{task_id[:8]}",
        ).start()
        return task_id

    def status(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            if task_id not in self._tasks:
                raise KeyError(task_id)
            return copy.deepcopy(self._tasks[task_id])

    def _set_task(self, task_id: str, **values: Any) -> None:
        with self._lock:
            self._tasks[task_id].update(values)

    def _locate(
        self, document: dict[str, Any], region_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
        found = next((item for item in iter_regions(document) if item[2].get("id") == region_id), None)
        if found is None:
            raise ValueError("Область разметки не найдена")
        return found

    def _set_region(self, source: Path, region_id: str, **values: Any) -> None:
        def mutate(document: dict[str, Any]) -> None:
            _obj, _owner, region, _kind = self._locate(document, region_id)
            region.update(values)

        self.store.mutate(source, mutate)

    @staticmethod
    def _locate_text(
        document: dict[str, Any], target_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], str, str]:
        for obj in document.get("objects") or []:
            for field in obj.get("fields") or []:
                if field.get("id") == target_id:
                    return obj, field, str(field.get("aiPrompt") or DEFAULT_PROMPT), "field"
            for collection, kind in (("columns", "column"), ("groups", "group")):
                for field in obj.get(collection) or []:
                    if field.get("id") == target_id:
                        return obj, field, str(field.get("aiPrompt") or DEFAULT_PROMPT), kind
            title = obj.get("title") or {}
            if title.get("id") == target_id:
                return obj, title, str(title.get("aiPrompt") or DEFAULT_PROMPT), "title"
            columns = {item.get("id"): item for item in obj.get("columns") or []}
            for row in obj.get("rows") or []:
                for cell in row.get("cells") or []:
                    if cell.get("id") == target_id:
                        column = columns.get(cell.get("columnId")) or {}
                        prompt = str(
                            column.get("cellPrompt")
                            or column.get("aiPrompt")
                            or DEFAULT_PROMPT
                        )
                        return obj, cell, prompt, "cell"
        raise ValueError("Поле для коррекции не найдено")

    def _recognize(
        self,
        source: Path,
        page: int,
        rect: dict[str, float],
        region: dict[str, Any],
        mode: str,
        orientation: int,
        prompt: str = "",
    ) -> str:
        text = self.paddle.recognize(
            render_crop(source, page, rect, orientation), mode
        )
        properties = region.get("properties") or {}
        if properties.get("useLlm"):
            text = self.llama.process(text, prompt.strip() or DEFAULT_PROMPT)
        return text

    @staticmethod
    def _sync_rows(obj: dict[str, Any], block: dict[str, Any], row_count: int) -> list[dict[str, Any]]:
        columns = obj.get("columns") or []
        current = [row for row in obj.get("rows") or [] if row.get("blockId") == block.get("id")]
        other = [row for row in obj.get("rows") or [] if row.get("blockId") != block.get("id")]
        rows: list[dict[str, Any]] = []
        for row_index in range(row_count):
            row = current[row_index] if row_index < len(current) else {
                "id": uid(), "blockId": block["id"], "cells": []
            }
            cells_by_column = {cell.get("columnId"): cell for cell in row.get("cells") or []}
            row["cells"] = [
                cells_by_column.get(column["id"])
                or {
                    "id": uid(),
                    "columnId": column["id"],
                    "value": "",
                    "orientation": None,
                    "sources": [],
                }
                for column in columns
            ]
            rows.append(row)
        obj["rows"] = other + rows
        block["rowIds"] = [row["id"] for row in rows]
        valid_ids = {row["id"] for row in obj["rows"]}
        for group in obj.get("groups") or []:
            group["rowIds"] = [row_id for row_id in group.get("rowIds", []) if row_id in valid_ids]
        return rows

    def _run(self, task_id: str, source: Path, region_id: str) -> None:
        try:
            self._set_task(task_id, state="running")
            self._set_region(source, region_id, status="running")
            snapshot = self.store.load(source)
            obj, owner, region, kind = self._locate(snapshot, region_id)
            page = int(region.get("page", 0))
            rect = validate_rect(region.get("rect"))
            output: Any
            if kind != "table_block":
                if kind == "cell":
                    column = next(
                        (
                            item
                            for item in obj.get("columns") or []
                            if item.get("id") == owner.get("columnId")
                        ),
                        {},
                    )
                    mode = str(
                        column.get("ocrMode")
                        or (region.get("properties") or {}).get("ocrMode")
                        or "text"
                    )
                    inherited_orientation = owner.get("orientation")
                    if inherited_orientation is None:
                        inherited_orientation = column.get(
                            "orientation", obj.get("orientation", 0)
                        )
                    correction_prompt = str(
                        column.get("cellPrompt")
                        or column.get("aiPrompt")
                        or DEFAULT_PROMPT
                    )
                else:
                    mode = str(owner.get("ocrMode") or "text")
                    inherited_orientation = owner.get("orientation", 0)
                    correction_prompt = str(owner.get("aiPrompt") or DEFAULT_PROMPT)
                if mode == "none":
                    raise ValueError("Для этого поля OCR отключён; заполните его вручную")
                orientation = normalize_orientation(
                    region.get("orientation", inherited_orientation)
                )
                output = self._recognize(
                    source,
                    page,
                    rect,
                    region,
                    mode,
                    orientation,
                    correction_prompt,
                )
            else:
                grid = owner.get("grid") or {}
                x_cuts = normalized_cuts(grid.get("columns"))
                y_cuts = normalized_cuts(grid.get("rows"))
                columns = obj.get("columns") or []
                if len(x_cuts) - 1 != len(columns):
                    raise ValueError(
                        "Число ячеек по горизонтали должно совпадать с числом заголовков таблицы"
                    )
                output = []
                rows = self._sync_rows(obj, owner, len(y_cuts) - 1)
                for row_index, (y0, y1) in enumerate(zip(y_cuts, y_cuts[1:])):
                    values: list[str] = []
                    for column_index, (x0, x1) in enumerate(zip(x_cuts, x_cuts[1:])):
                        column = columns[column_index]
                        cell = rows[row_index]["cells"][column_index]
                        orientation = cell.get("orientation")
                        if orientation is None:
                            orientation = column.get(
                                "orientation", obj.get("orientation", 0)
                            )
                        values.append(
                            self._recognize(
                                source,
                                page,
                                cell_rect(rect, x0, x1, y0, y1),
                                region,
                                str(column.get("ocrMode") or "text"),
                                normalize_orientation(orientation),
                                str(
                                    column.get("cellPrompt")
                                    or column.get("aiPrompt")
                                    or DEFAULT_PROMPT
                                ),
                            )
                        )
                    output.append(values)

            def save_output(document: dict[str, Any]) -> None:
                target_obj, target_owner, target, target_kind = self._locate(
                    document, region_id
                )
                # A geometry or value edit made while OCR was running wins over the
                # stale background result.  The user can explicitly repeat OCR.
                if target.get("status") == "modified":
                    target["error"] = None
                    return
                target.update(
                    {
                        "status": "recognized",
                        "output": output,
                        "error": None,
                        "updated": time.time(),
                    }
                )
                if target_kind != "table_block":
                    target_owner["value"] = combine_ocr_outputs(
                        target_owner.get("sources") or []
                    )
                else:
                    rows = self._sync_rows(target_obj, target_owner, len(output))
                    for row, values in zip(rows, output):
                        for cell, value in zip(row.get("cells") or [], values):
                            cell["value"] = str(value)

            document = self.store.mutate(source, save_output)
            self._set_task(task_id, state="done", document=document)
        except Exception as exc:
            try:
                self._set_region(source, region_id, status="error", error=str(exc))
            finally:
                self._set_task(task_id, state="failed", error=str(exc))

    def _run_correction(self, task_id: str, source: Path, target_id: str) -> None:
        try:
            self._set_task(task_id, state="running")
            snapshot = self.store.load(source)
            _obj, target, prompt, _kind = self._locate_text(snapshot, target_id)
            original = str(target.get("value") or "")
            if not original.strip():
                raise ValueError("Нечего корректировать: значение пусто")
            corrected = self.llama.process(original, prompt)
            applied = False

            def save_output(document: dict[str, Any]) -> None:
                nonlocal applied
                obj, current, _prompt, kind = self._locate_text(document, target_id)
                if str(current.get("value") or "") != original:
                    return
                current["value"] = corrected
                current["aiUpdated"] = time.time()
                sources = list(current.get("sources") or [])
                if kind == "cell" and not sources:
                    row = next(
                        (
                            item
                            for item in obj.get("rows") or []
                            if current in (item.get("cells") or [])
                        ),
                        None,
                    )
                    block = next(
                        (
                            item
                            for item in obj.get("blocks") or []
                            if item.get("id") == (row or {}).get("blockId")
                        ),
                        None,
                    )
                    if block and block.get("region"):
                        sources.append(block["region"])
                for region in sources:
                    region.update({"status": "modified", "error": None})
                applied = True

            document = self.store.mutate(source, save_output)
            self._set_task(
                task_id,
                state="done",
                document=document,
                applied=applied,
                targetId=target_id,
            )
        except Exception as exc:
            self._set_task(task_id, state="failed", error=str(exc), targetId=target_id)


def reset_template_objects(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    copied = copy.deepcopy(objects)
    for obj in copied:
        if obj.get("type") == "record":
            fields = obj.get("fields") or []
        else:
            fields = (
                ([obj["title"]] if obj.get("title") else [])
                + (obj.get("columns") or [])
                + (obj.get("groups") or [])
            )
            for row in obj.get("rows") or []:
                for cell in row.get("cells") or []:
                    cell["value"] = ""
                    for source in cell.get("sources") or []:
                        source.update(
                            {"status": "pending", "output": None, "error": None}
                        )
        for field in fields:
            field["value"] = ""
            for source in field.get("sources") or []:
                source.update({"status": "pending", "output": None, "error": None})
        for block in obj.get("blocks") or []:
            block["region"].update({"status": "pending", "output": None, "error": None})
    return copied


def normalize_template_catalog(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"schema": SCHEMA_VERSION, "templates": []}
    catalog = copy.deepcopy(value)
    if catalog.get("schema", 1) == 1:
        for template in catalog.get("templates") or []:
            migrated = migrate_v1(
                {"schema": 1, "objects": template.get("objects") or []}
            )
            template["objects"] = migrated["objects"]
        catalog["schema"] = SCHEMA_VERSION
    catalog.setdefault("templates", [])
    return catalog


def choose_pdf(initial_directory: Path | None = None) -> str | None:
    if os.name != "nt":
        raise RuntimeError("Системный диалог выбора PDF доступен только в Windows")
    initial = initial_directory or Path.cwd()
    try:
        initial = initial.resolve(strict=True)
    except OSError:
        initial = Path.cwd().resolve()
    if not initial.is_dir():
        initial = initial.parent
    encoded_initial = base64.b64encode(str(initial).encode("utf-8")).decode("ascii")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d=New-Object System.Windows.Forms.OpenFileDialog;"
        "$d.Filter='PDF (*.pdf)|*.pdf';$d.Multiselect=$false;"
        f"$i=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_initial}'));"
        "$d.InitialDirectory=$i;$d.RestoreDirectory=$true;$d.CheckPathExists=$true;"
        "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){"
        "$b=[Text.Encoding]::UTF8.GetBytes($d.FileName);"
        "[Console]::Out.WriteLine([Convert]::ToBase64String($b))}"
    )
    executable = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    completed = subprocess.run(
        [str(executable), "-NoProfile", "-STA", "-EncodedCommand", encoded],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=600,
        check=False,
    )
    if completed.returncode:
        encoding = locale.getpreferredencoding(False) or "utf-8"
        raise RuntimeError(completed.stderr.decode(encoding, errors="replace"))
    lines = completed.stdout.decode("ascii", errors="ignore").strip().splitlines()
    return base64.b64decode(lines[-1]).decode("utf-8") if lines else None


def create_app(
    registry: SourceRegistry,
    store: DocumentStore,
    tasks: ExtractionTasks,
    html_path: Path,
):
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import HTMLResponse, JSONResponse, Response

    app = FastAPI(title="Semantic PDF digitizer")

    def bad(exc: Exception) -> HTTPException:
        return HTTPException(status_code=400, detail=str(exc))

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return html_path.read_text(encoding="utf-8")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ready", "schema": SCHEMA_VERSION, "ocr": tasks.paddle.health()}

    @app.get("/api/catalog")
    def catalog() -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "defaultPrompt": DEFAULT_PROMPT,
            "presets": preset_catalog(),
            "ocrModes": sorted(OCR_MODES),
            "ocr": tasks.paddle.health(),
        }

    @app.post("/api/open/dialog")
    def open_dialog(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            initial = registry.workspace or Path.cwd()
            raw_source = str((payload or {}).get("source") or "").strip()
            if raw_source and not urlparse(raw_source).scheme:
                candidate = Path(raw_source).expanduser()
                if candidate.is_file():
                    initial = candidate.parent
                elif candidate.is_dir():
                    initial = candidate
            selected = choose_pdf(initial)
            return {"cancelled": selected is None, "source": selected}
        except Exception as exc:
            raise bad(exc) from exc

    @app.get("/api/document")
    def get_document(source: str = Query(...)) -> dict[str, Any]:
        try:
            path = registry.resolve(source)
            return {"source": str(path), "name": path.name, "document": store.load(path)}
        except Exception as exc:
            raise bad(exc) from exc

    @app.put("/api/document")
    def put_document(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            source = registry.resolve(str(payload.get("source") or ""))
            document = payload.get("document")
            if not isinstance(document, dict):
                raise ValueError("document должен быть JSON-объектом")
            return {
                "document": store.save(source, document),
                "path": str(sidecar_path(source)),
            }
        except Exception as exc:
            raise bad(exc) from exc

    @app.get("/api/pdf/page/{page_number}")
    def pdf_page(page_number: int, source: str = Query(...)) -> Response:
        try:
            path = registry.resolve(source)
            pymupdf = import_pymupdf()
            with pymupdf.open(path) as pdf:
                if page_number < 1 or page_number > pdf.page_count:
                    raise ValueError("Страница не найдена")
                page = pdf.load_page(page_number - 1)
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
                return Response(pixmap.tobytes("png"), media_type="image/png")
        except Exception as exc:
            raise bad(exc) from exc

    @app.post("/api/ocr")
    def run_ocr(payload: dict[str, Any]) -> dict[str, str]:
        try:
            source = registry.resolve(str(payload.get("source") or ""))
            return {"taskId": tasks.submit(source, str(payload.get("regionId") or ""))}
        except Exception as exc:
            raise bad(exc) from exc

    @app.post("/api/correct")
    def correct_text(payload: dict[str, Any]) -> dict[str, str]:
        try:
            source = registry.resolve(str(payload.get("source") or ""))
            target_id = str(payload.get("targetId") or "")
            if not target_id:
                raise ValueError("Не указано поле для коррекции")
            return {"taskId": tasks.submit_correction(source, target_id)}
        except Exception as exc:
            raise bad(exc) from exc

    @app.get("/api/tasks/{task_id}")
    def task_status(task_id: str) -> dict[str, Any]:
        try:
            return tasks.status(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Задание не найдено") from exc

    @app.get("/api/templates")
    def templates(source: str = Query(...)) -> dict[str, Any]:
        try:
            path = template_path(registry.resolve(source))
            try:
                values = normalize_template_catalog(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError):
                values = {"schema": SCHEMA_VERSION, "templates": []}
            return values
        except Exception as exc:
            raise bad(exc) from exc

    @app.post("/api/templates")
    def save_template(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            source = registry.resolve(str(payload.get("source") or ""))
            name = str(payload.get("name") or "").strip()
            document = normalize_document(payload.get("document") or {})
            if not name:
                raise ValueError("Укажите название шаблона")
            path = template_path(source)
            try:
                catalog_value = normalize_template_catalog(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError):
                catalog_value = {"schema": SCHEMA_VERSION, "templates": []}
            template = {
                "id": uid(),
                "name": name,
                "objects": reset_template_objects(document.get("objects") or []),
                "created": time.time(),
            }
            catalog_value["schema"] = SCHEMA_VERSION
            catalog_value.setdefault("templates", []).append(template)
            atomic_json(path, catalog_value)
            return catalog_value
        except Exception as exc:
            raise bad(exc) from exc

    @app.post("/api/templates/apply")
    def apply_template(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            source = registry.resolve(str(payload.get("source") or ""))
            template_id = str(payload.get("templateId") or "")
            catalog_value = normalize_template_catalog(
                json.loads(template_path(source).read_text(encoding="utf-8"))
            )
            template = next(
                (item for item in catalog_value.get("templates") or [] if item.get("id") == template_id),
                None,
            )
            if template is None:
                raise ValueError("Шаблон не найден")
            document = store.load(source)
            document["templateId"] = template_id
            document["objects"] = reset_template_objects(template.get("objects") or [])
            return {"document": store.save(source, document)}
        except Exception as exc:
            raise bad(exc) from exc

    @app.get("/api/export")
    def export(source: str = Query(...)) -> JSONResponse:
        try:
            path = registry.resolve(source)
            document = store.load(path)
            return JSONResponse(
                document,
                headers={"Content-Disposition": f'attachment; filename="{path.stem}.json"'},
            )
        except Exception as exc:
            raise bad(exc) from exc

    return app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9400)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--paddle-url", default="http://127.0.0.1:9399")
    parser.add_argument("--paddle-token", required=True)
    parser.add_argument("--llama-url", default="http://127.0.0.1:6381/v1")
    parser.add_argument("--llama-key", default="")
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        raise SystemExit("Digitizer may bind only to 127.0.0.1")
    registry = SourceRegistry(args.data_root)
    store = DocumentStore()
    tasks = ExtractionTasks(
        store,
        PaddleClient(args.paddle_url, args.paddle_token),
        LlamaClient(args.llama_url, args.llama_key),
    )
    app = create_app(registry, store, tasks, Path(__file__).with_name("app.html"))
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
