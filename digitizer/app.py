"""Minimal, loopback-only PDF digitizer.

The Markdown sidecar is the complete document database.  HTML comments bind each
piece of Markdown to a rectangle in the source PDF; object/field types only add
meaning and never change OCR behaviour.
"""

from __future__ import annotations

import argparse
import base64
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
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import yaml

PDF_SUFFIX = ".pdf"
MAX_PDF_BYTES = 1 << 30
STATUSES = {"pending", "queued", "running", "recognized", "modified", "error"}
ORIENTATIONS = {0, 90, 180, 270}
MARKER_RE = re.compile(
    r"<!--[ \t]*digitizer:(object|region|table)[ \t]+(\{[^\r\n]*\})[ \t]*-->",
    re.IGNORECASE,
)


class EmptyOcrResult(RuntimeError):
    """The OCR service completed normally but returned no text."""


def load_digitizer_config(path: Path | None = None) -> dict[str, Any]:
    """Load the deliberately small domain schema from ``config.yaml``."""
    config_path = path or Path(__file__).with_name("config.yaml")
    with config_path.open("r", encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    if not isinstance(raw, dict) or set(raw) != {"types"}:
        raise RuntimeError(f"{config_path} должен содержать только корневой ключ types")
    raw_types = raw["types"]
    if not isinstance(raw_types, dict) or not raw_types:
        raise RuntimeError(f"В {config_path} нужен непустой словарь types")

    types: dict[str, dict[str, Any]] = {}
    for type_key, raw_type in raw_types.items():
        key = str(type_key).strip()
        if not key or not isinstance(raw_type, dict):
            raise RuntimeError("Каждому типу нужен непустой ключ и YAML-объект")
        name = str(raw_type.get("name") or "").strip()
        fields = raw_type.get("fields")
        if not name or not isinstance(fields, dict) or not fields:
            raise RuntimeError(f"Типу {key} нужны name и непустой словарь fields")
        normalized_fields: dict[str, dict[str, str]] = {}
        for field_key, raw_field in fields.items():
            field_key = str(field_key).strip()
            if not field_key or not isinstance(raw_field, dict):
                raise RuntimeError(f"Некорректное поле типа {key}")
            field_name = str(raw_field.get("name") or "").strip()
            prompt = str(raw_field.get("prompt") or "").strip()
            kind = str(raw_field.get("kind") or "text").strip()
            if not field_name or not prompt or kind not in {"text", "grid"}:
                raise RuntimeError(
                    f"Полю {key}.{field_key} нужны name, prompt и kind text/grid"
                )
            normalized_fields[field_key] = {
                "name": field_name,
                "kind": kind,
                "prompt": prompt,
            }
        types[key] = {"name": name, "fields": normalized_fields}
    return {"types": types}


DIGITIZER_CONFIG = load_digitizer_config()


def public_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return UI metadata without duplicating correction prompts in the browser."""
    config = config or DIGITIZER_CONFIG
    return {
        "types": {
            type_key: {
                "name": value["name"],
                "fields": {
                    field_key: {"name": field["name"], "kind": field["kind"]}
                    for field_key, field in value["fields"].items()
                },
            }
            for type_key, value in config["types"].items()
        }
    }


def import_pymupdf():
    try:
        import pymupdf

        return pymupdf
    except ImportError:
        import fitz

        return fitz


def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}: ожидалось число") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label}: ожидалось конечное число")
    return number


def validate_rect(value: Any) -> list[float]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("rect должен быть массивом [x, y, width, height]")
    x, y, width, height = (_finite_number(item, "rect") for item in value)
    if (
        x < 0
        or y < 0
        or width <= 0
        or height <= 0
        or x + width > 1.000001
        or y + height > 1.000001
    ):
        raise ValueError("Прямоугольник должен находиться внутри страницы")
    return [round(x, 6), round(y, 6), round(width, 6), round(height, 6)]


def normalize_rotation(value: Any) -> int:
    try:
        rotation = int(value or 0) % 360
    except (TypeError, ValueError) as exc:
        raise ValueError("rotation должен быть целым числом") from exc
    if rotation not in ORIENTATIONS:
        raise ValueError("rotation должен быть 0, 90, 180 или 270")
    return rotation


def normalize_cuts(value: Any, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) < 2:
        raise ValueError(f"{label} должен содержать минимум [0, 1]")
    cuts = [round(_finite_number(item, label), 6) for item in value]
    if (
        cuts[0] != 0
        or cuts[-1] != 1
        or any(left >= right for left, right in pairwise(cuts))
    ):
        raise ValueError(f"{label} должен строго возрастать от 0 до 1")
    return cuts


def _status_matrix(value: Any, rows: int, columns: int) -> list[list[str]]:
    matrix: list[list[str]] = []
    for row_index in range(rows):
        source_row = (
            value[row_index]
            if isinstance(value, list) and row_index < len(value)
            else []
        )
        row: list[str] = []
        for column_index in range(columns):
            status = (
                str(source_row[column_index])
                if isinstance(source_row, list)
                and column_index < len(source_row)
                and str(source_row[column_index]) in STATUSES
                else "pending"
            )
            row.append("pending" if status in {"queued", "running"} else status)
        matrix.append(row)
    return matrix


def normalize_region(
    value: Any,
    marker_kind: str,
    object_type: str,
    config: dict[str, Any],
    page_count: int | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("Маркер области должен содержать JSON-объект")
    field = str(value.get("field") or "").strip()
    fields = config["types"][object_type]["fields"]
    if field not in fields:
        raise ValueError(f"Неизвестное поле {object_type}.{field}")
    expected_kind = "table" if fields[field]["kind"] == "grid" else "region"
    if marker_kind != expected_kind:
        raise ValueError(f"Поле {object_type}.{field} требует маркер {expected_kind}")
    try:
        page = int(value.get("page"))
    except (TypeError, ValueError) as exc:
        raise ValueError("page должен быть номером страницы") from exc
    if page < 1 or page_count is not None and page > page_count:
        raise ValueError("Область ссылается на несуществующую страницу")
    status = str(value.get("status") or "pending")
    if status not in STATUSES:
        raise ValueError(f"Неизвестный статус {status}")
    if status in {"queued", "running"}:
        status = "pending"
    region: dict[str, Any] = {
        "field": field,
        "page": page,
        "rect": validate_rect(value.get("rect")),
        "rotation": normalize_rotation(value.get("rotation")),
        "status": status,
    }
    if status == "error" and str(value.get("error") or "").strip():
        region["error"] = str(value["error"]).strip()
    if marker_kind == "table":
        raw_grid = value.get("grid")
        if not isinstance(raw_grid, dict):
            raise ValueError("Табличной области нужен grid")
        x_cuts = normalize_cuts(raw_grid.get("x"), "grid.x")
        y_cuts = normalize_cuts(raw_grid.get("y"), "grid.y")
        rows, columns = len(y_cuts) - 1, len(x_cuts) - 1
        region["grid"] = {
            "x": x_cuts,
            "y": y_cuts,
            "statuses": _status_matrix(raw_grid.get("statuses"), rows, columns),
        }
    return region


def parse_markdown(
    markdown: str,
    config: dict[str, Any] | None = None,
    page_count: int | None = None,
) -> list[dict[str, Any]]:
    """Parse canonical marker comments while leaving the following Markdown raw."""
    config = config or DIGITIZER_CONFIG
    matches = list(MARKER_RE.finditer(markdown))
    if not matches:
        if markdown.strip():
            raise ValueError("Markdown не содержит маркеров digitizer")
        return []
    if markdown[: matches[0].start()].strip():
        raise ValueError("Текст до первого маркера digitizer не поддерживается")
    objects: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for index, match in enumerate(matches):
        marker_kind = match.group(1).lower()
        try:
            metadata = json.loads(match.group(2))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Некорректный JSON в маркере: {exc.msg}") from exc
        content_end = (
            matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        )
        content = markdown[match.end() : content_end].strip("\r\n")
        if marker_kind == "object":
            if content.strip():
                raise ValueError(
                    "После маркера object сразу должен идти маркер области"
                )
            if not isinstance(metadata, dict):
                raise ValueError("Маркер object должен содержать JSON-объект")
            object_type = str(metadata.get("type") or "").strip()
            if object_type not in config["types"]:
                raise ValueError(f"Неизвестный тип объекта {object_type}")
            current = {"type": object_type, "regions": []}
            objects.append(current)
            continue
        if current is None:
            raise ValueError("Маркер области найден до маркера object")
        region = normalize_region(
            metadata, marker_kind, current["type"], config, page_count
        )
        region["kind"] = marker_kind
        region["text"] = content
        current["regions"].append(region)
    return objects


def serialize_markdown(
    objects: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
    page_count: int | None = None,
) -> str:
    config = config or DIGITIZER_CONFIG
    chunks: list[str] = []
    for obj in objects:
        if not isinstance(obj, dict):
            raise TypeError("Объект документа повреждён")
        object_type = str(obj.get("type") or "")
        if object_type not in config["types"]:
            raise ValueError(f"Неизвестный тип объекта {object_type}")
        lines = [
            "<!-- digitizer:object "
            + json.dumps(
                {"type": object_type}, ensure_ascii=False, separators=(",", ":")
            )
            + " -->"
        ]
        for raw_region in obj.get("regions") or []:
            marker_kind = str(raw_region.get("kind") or "region")
            region = normalize_region(
                raw_region, marker_kind, object_type, config, page_count
            )
            metadata = {key: value for key, value in region.items() if key != "kind"}
            lines.append(
                "<!-- digitizer:"
                + marker_kind
                + " "
                + json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
                + " -->"
            )
            text = str(raw_region.get("text") or "").strip("\r\n")
            if text:
                lines.append(text)
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks).rstrip() + ("\n" if chunks else "")


def sidecar_path(source: Path) -> Path:
    return source.with_name(source.name + ".digitizer.md")


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


class DocumentStore:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or DIGITIZER_CONFIG
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()

    def _lock(self, source: Path) -> threading.RLock:
        key = str(source).casefold() if os.name == "nt" else str(source)
        with self._guard:
            return self._locks.setdefault(key, threading.RLock())

    @staticmethod
    def page_count(source: Path) -> int:
        pymupdf = import_pymupdf()
        with pymupdf.open(source) as pdf:
            return pdf.page_count

    def load(self, source: Path) -> dict[str, Any]:
        with self._lock(source):
            count = self.page_count(source)
            try:
                markdown = sidecar_path(source).read_text(encoding="utf-8")
            except FileNotFoundError:
                markdown = ""
            objects = parse_markdown(markdown, self.config, count)
            normalized = serialize_markdown(objects, self.config, count)
            return {"markdown": normalized, "objects": objects, "pageCount": count}

    def save(self, source: Path, markdown: str) -> dict[str, Any]:
        if not isinstance(markdown, str):
            raise TypeError("markdown должен быть строкой")
        with self._lock(source):
            count = self.page_count(source)
            objects = parse_markdown(markdown, self.config, count)
            normalized = serialize_markdown(objects, self.config, count)
            atomic_text(sidecar_path(source), normalized)
            return {"markdown": normalized, "objects": objects, "pageCount": count}


class SourceRegistry:
    """Resolve local PDF paths and cache explicitly opened HTTP PDF sources."""

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.index_path = data_root / "remote-sources.json"
        self._lock = threading.Lock()
        try:
            self.remote_index = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.remote_index: dict[str, str] = {}

    def resolve(self, value: str) -> Path:
        value = unquote(value.strip())
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"}:
            return self._download(value)
        source = Path(value).expanduser().resolve(strict=True)
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
            bucket = self.data_root / "blob-cache" / uuid.uuid4().hex
            bucket.mkdir(parents=True)
            name = Path(urlparse(url).path).name or "document.pdf"
            if not name.lower().endswith(PDF_SUFFIX):
                name += PDF_SUFFIX
            destination = bucket / name
            total = 0
            with httpx.stream(
                "GET", url, timeout=120.0, follow_redirects=True
            ) as response:
                response.raise_for_status()
                with destination.open("wb") as output:
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > MAX_PDF_BYTES:
                            raise ValueError("PDF из blob-хранилища превышает 1 ГБ")
                        output.write(chunk)
            with destination.open("rb") as downloaded:
                signature = downloaded.read(5)
            if signature != b"%PDF-":
                destination.unlink(missing_ok=True)
                raise ValueError("Удалённый источник вернул не PDF")
            self.remote_index[url] = destination.relative_to(self.data_root).as_posix()
            atomic_text(
                self.index_path,
                json.dumps(self.remote_index, ensure_ascii=False, indent=2) + "\n",
            )
            return destination


def clean_ocr_markup(value: str) -> str:
    value = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", value, flags=re.DOTALL)
    value = re.sub(r"<img\b[^>]*>", "", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(
        r"</?(?:div|p|span)\b[^>]*>",
        "",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(r"(?m)^\s{0,3}#{1,6}[ \t]+", "", value)
    return "\n".join(
        line.rstrip() for line in html.unescape(value).strip().splitlines()
    )


def _recognized_texts(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    texts = value.get("rec_texts") or value.get("recTexts") or []
    if not isinstance(texts, list):
        return []
    return [text for item in texts if (text := clean_ocr_markup(str(item)))]


def flatten_ocr_result(value: str) -> str:
    general: list[str] = []
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
        if isinstance(markdown, str) and (cleaned := clean_ocr_markup(markdown)):
            markdown_parts.append(cleaned)
        for layout in result.get("layoutParsingResults") or []:
            pruned = layout.get("prunedResult") or {}
            general.extend(_recognized_texts(pruned.get("overall_ocr_res")))
            for block in pruned.get("parsing_res_list") or []:
                if not isinstance(block, dict):
                    continue
                label = str(block.get("block_label") or "").casefold()
                content = clean_ocr_markup(str(block.get("block_content") or ""))
                if content and not any(
                    token in label for token in ("image", "figure", "chart")
                ):
                    fallback.append(content)
    parts = general or fallback or markdown_parts
    if not parts:
        raise EmptyOcrResult("PaddleOCR вернул пустой результат")
    return "\n".join(parts).strip()


class PaddleClient:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token

    def recognize(self, image: bytes) -> str:
        import httpx

        options = {
            "useTableRecognition": False,
            "useSealRecognition": False,
            "useFormulaRecognition": False,
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
            self._raise_error(response)
            job_id = response.json()["data"]["jobId"]
            deadline = time.monotonic() + 3600
            while time.monotonic() < deadline:
                status = client.get(
                    f"{self.url}/api/v2/ocr/jobs/{job_id}", headers=headers
                )
                self._raise_error(status)
                payload = status.json()["data"]
                if payload["state"] == "done":
                    result = client.get(
                        f"{self.url}/api/v2/ocr/jobs/{job_id}/result", headers=headers
                    )
                    self._raise_error(result)
                    return flatten_ocr_result(result.text)
                if payload["state"] == "failed":
                    raise RuntimeError(payload.get("errorMsg") or "Ошибка PaddleOCR")
                time.sleep(0.5)
        raise TimeoutError("PaddleOCR не завершил область за один час")

    @staticmethod
    def _raise_error(response: Any) -> None:
        if not response.is_error:
            return
        try:
            payload = response.json()
            detail = payload.get("detail") if isinstance(payload, dict) else None
        except ValueError:
            detail = None
        raise RuntimeError(
            str(detail or response.text or f"HTTP {response.status_code}").strip()
        )


class OpenAICompatibleClient:
    def __init__(self, url: str, api_key: str = "") -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key

    def correct(self, text: str, prompt: str) -> str:
        """Return model content verbatim: the caller deliberately performs no checks."""
        import httpx

        if not self.url:
            raise RuntimeError("OpenAI-совместимый API не настроен")
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        with httpx.Client(
            timeout=httpx.Timeout(30.0, read=600.0), headers=headers
        ) as client:
            models = client.get(f"{self.url}/models")
            models.raise_for_status()
            model = models.json().get("data", [{}])[0].get("id", "local-model")
            response = client.post(
                f"{self.url}/chat/completions",
                json={
                    "model": model,
                    "temperature": 0,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": text},
                    ],
                },
            )
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"])


def render_crop(
    source: Path, page_number: int, rect: list[float], rotation: int = 0
) -> bytes:
    pymupdf = import_pymupdf()
    with pymupdf.open(source) as pdf:
        if page_number < 1 or page_number > pdf.page_count:
            raise ValueError("Страница области не существует")
        page = pdf.load_page(page_number - 1)
        bounds = page.rect
        x, y, width, height = validate_rect(rect)
        clip = pymupdf.Rect(
            bounds.x0 + x * bounds.width,
            bounds.y0 + y * bounds.height,
            bounds.x0 + (x + width) * bounds.width,
            bounds.y0 + (y + height) * bounds.height,
        )
        matrix = pymupdf.Matrix(2.5, 2.5)
        if rotation:
            matrix = matrix.prerotate(normalize_rotation(rotation))
        return page.get_pixmap(matrix=matrix, clip=clip, alpha=False).tobytes("png")


def choose_pdf() -> str | None:
    if os.name != "nt":
        raise RuntimeError("Системный диалог выбора PDF доступен только в Windows")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$owner=New-Object System.Windows.Forms.Form;"
        "$owner.ShowInTaskbar=$false;$owner.TopMost=$true;$owner.Opacity=0;"
        "$owner.Width=1;$owner.Height=1;"
        "$d=New-Object System.Windows.Forms.OpenFileDialog;"
        "$d.Filter='PDF (*.pdf)|*.pdf';$d.Multiselect=$false;"
        "$d.AutoUpgradeEnabled=$true;$d.RestoreDirectory=$true;$d.CheckPathExists=$true;"
        "$owner.Show();$owner.Activate();"
        "if($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK){"
        "$b=[Text.Encoding]::UTF8.GetBytes($d.FileName);"
        "[Console]::Out.WriteLine([Convert]::ToBase64String($b))};"
        "$owner.Close();$owner.Dispose();$d.Dispose()"
    )
    executable = (
        Path(os.environ["SystemRoot"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    completed = subprocess.run(
        [str(executable), "-NoProfile", "-STA", "-EncodedCommand", encoded],
        capture_output=True,
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
    paddle: PaddleClient,
    llm: OpenAICompatibleClient,
    html_path: Path,
):
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import HTMLResponse, Response

    app = FastAPI(title="PDF Digitizer")

    def bad(exc: Exception) -> HTTPException:
        return HTTPException(status_code=400, detail=str(exc))

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return html_path.read_text(encoding="utf-8")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return public_config(store.config)

    @app.post("/api/open/dialog")
    def open_dialog() -> dict[str, Any]:
        try:
            selected = choose_pdf()
            return {"cancelled": selected is None, "source": selected}
        except Exception as exc:
            raise bad(exc) from exc

    @app.get("/api/document")
    def get_document(source: str = Query(...)) -> dict[str, Any]:
        try:
            path = registry.resolve(source)
            return {"source": str(path), "name": path.name, **store.load(path)}
        except Exception as exc:
            raise bad(exc) from exc

    @app.put("/api/document")
    def put_document(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            source = registry.resolve(str(payload.get("source") or ""))
            result = store.save(source, payload.get("markdown"))
            return {**result, "path": str(sidecar_path(source))}
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
            page = int(payload.get("page"))
            rect = validate_rect(payload.get("rect"))
            rotation = normalize_rotation(payload.get("rotation"))
            return {"text": paddle.recognize(render_crop(source, page, rect, rotation))}
        except Exception as exc:
            raise bad(exc) from exc

    @app.post("/api/correct")
    def correct(payload: dict[str, Any]) -> dict[str, str]:
        try:
            object_type = str(payload.get("type") or "")
            field = str(payload.get("field") or "")
            prompt = store.config["types"][object_type]["fields"][field]["prompt"]
            text = str(payload.get("text") or "")
            if not text.strip():
                raise ValueError("Нечего корректировать")
            return {"text": llm.correct(text, prompt)}
        except KeyError as exc:
            raise bad(ValueError("Неизвестный тип объекта или поля")) from exc
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
    app = create_app(
        SourceRegistry(args.data_root),
        DocumentStore(),
        PaddleClient(args.paddle_url, args.paddle_token),
        OpenAICompatibleClient(args.llama_url, args.llama_key),
        Path(__file__).with_name("app.html"),
    )
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
