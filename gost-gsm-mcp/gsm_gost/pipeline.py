from __future__ import annotations

import csv
import os
import re
import unicodedata
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .storage import (
    read_json,
    read_jsonl,
    replace_batch_records,
    write_json,
    write_jsonl,
)
from .tables import (
    clean_text,
    collect_source_files,
    comparison_text,
    evaluate_table,
    extract_tables,
    inline_text,
)


BRAND_ROLES = ("main", "duplicate", "reserve", "foreign")
RELATION_TYPES = {
    "duplicate": "Дублирующая",
    "reserve": "Резервная",
    "foreign": "Зарубежная",
}
VALID_RELATION_TYPES = frozenset(RELATION_TYPES.values())

DEFAULT_CONFIG: dict[str, Any] = {
    "default_batch_size": 10,
    "max_batch_size": 20,
    "normative_document_prefixes": [
        "ГОСТ Р ИСО",
        "ГОСТ Р",
        "ГОСТ",
        "ОСТ",
        "СТО",
        "ТУ У",
        "ТУ",
        "ISO",
        "ASTM",
        "DIN",
        "EN",
        "MIL",
        "API",
    ],
    "header_aliases": {
        "main": ["основная марка гсм", "основная марка", "основная"],
        "duplicate": [
            "дублирующая марка гсм",
            "дублирующая марка",
            "дублирующая",
            "дубл.",
        ],
        "reserve": ["резервная марка гсм", "резервная марка", "резервная"],
        "foreign": [
            "зарубежная марка гсм",
            "зарубежная марка",
            "зарубежная",
            "иностранная марка",
            "импортный аналог",
        ],
        "group": ["группа гсм", "группа"],
        "subgroup": ["подгруппа гсм", "подгруппа"],
        "ground": ["наземная техника", "наземная", "наземный"],
        "aviation": ["авиационная техника", "авиационная", "авиация"],
        "marine": ["морская техника", "морская", "морской", "флот"],
        "note": ["примечание", "примечания", "прим."],
        "brand": ["марка гсм", "марка", "наименование гсм", "наименование"],
        "purpose": [
            "назначение и условия применения",
            "назначение",
            "условия применения",
        ],
        "normdoc": [
            "нормативный документ",
            "обозначение нормативного документа",
            "нормативная документация",
            "нд",
        ],
    },
}


class PipelineError(RuntimeError):
    """A safe, user-facing pipeline failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def normalized_brand(value: str) -> str:
    text = unicodedata.normalize("NFKC", inline_text(value))
    return comparison_text(text)


def brand_key(brand: str, normative_document: str | None) -> str:
    document = normative_document if normative_document else "∅"
    return f"{inline_text(brand)} || {inline_text(document)}"


def normalized_brand_key(brand: str, normative_document: str | None) -> str:
    return f"{normalized_brand(brand)} || {comparison_text(normative_document or '∅')}"


def parse_equipment(value: Any) -> bool | None:
    text = comparison_text(value)
    if not text:
        return None
    if text in {"+", "да", "есть", "true", "1", "x", "х"}:
        return True
    if text in {"-", "нет", "false", "0", "—", "–"}:
        return False
    return None


def equipment_export(value: Any) -> str:
    if value is True:
        return "+"
    if value is False:
        return "-"
    return ""


def _prefix_pattern(config: dict[str, Any]) -> str:
    prefixes = sorted(
        config["normative_document_prefixes"], key=lambda value: (-len(value), value)
    )
    return (
        "(?:"
        + "|".join(re.escape(value).replace(r"\ ", r"\s+") for value in prefixes)
        + ")"
    )


def split_cell_items(raw_cell: str, config: dict[str, Any]) -> list[str]:
    text = clean_text(raw_cell)
    if not text:
        return []
    text = re.sub(r"(?m)^\s*(?:[•·▪◦*]|\d+[.)])\s*", "", text)
    text = re.sub(r"\s+(?=\d+[.)]\s+)", "\n", text)
    prefix = re.compile(rf"\b{_prefix_pattern(config)}\b", re.IGNORECASE)
    items: list[str] = []
    for line in (part.strip(" ;") for part in text.splitlines()):
        if not line:
            continue
        if ";" in line and len(prefix.findall(line)) > 1:
            items.extend(
                part.strip(" ;") for part in line.split(";") if part.strip(" ;")
            )
        else:
            items.append(line)
    return items or [inline_text(text)]


def parse_brand_item(
    raw_item: str,
    config: dict[str, Any],
    normative_hint: str | None = None,
) -> dict[str, Any]:
    raw_item = inline_text(raw_item).strip(" ;")
    prefix = _prefix_pattern(config)
    primary = re.compile(
        rf"^(?P<brand>.+?)\s+по\s+(?P<document>{prefix}(?:\s|\S)*)$", re.IGNORECASE
    )
    fallback = re.compile(
        rf"^(?P<brand>.+?)(?:,|\s)+(?P<document>{prefix}(?:\s|\S)*)$", re.IGNORECASE
    )
    flags: list[str] = []
    tokens = re.findall(r"[0-9A-Za-zА-Яа-яЁё_-]+", raw_item)
    if any(
        re.search(r"[A-Za-z]", token) and re.search(r"[А-Яа-яЁё]", token)
        for token in tokens
    ):
        flags.append("mixed_latin_cyrillic")
    if len(re.findall(rf"\b{prefix}\b", raw_item, flags=re.IGNORECASE)) > 1:
        flags.append("multiple_normdocs_in_item")

    match = primary.match(raw_item)
    if match:
        brand = inline_text(match.group("brand")).strip(" ,;")
        normative_document = inline_text(match.group("document")).strip(" ,;")
    else:
        match = fallback.match(raw_item)
        if match:
            brand = inline_text(match.group("brand")).strip(" ,;")
            normative_document = inline_text(match.group("document")).strip(" ,;")
            flags.append("normdoc_without_po")
        else:
            brand = raw_item
            normative_document = None
            flags.append("normdoc_not_found")

    hint = inline_text(normative_hint) if normative_hint else ""
    if hint and not normative_document:
        normative_document = hint
        flags = [flag for flag in flags if flag != "normdoc_not_found"]
        flags.append("normdoc_from_separate_column")
    elif hint and comparison_text(hint) != comparison_text(normative_document):
        flags.append("normdoc_conflict_with_separate_column")

    if not brand:
        flags.append("empty_brand")
    return {
        "brand": brand,
        "normative_document": normative_document,
        "brand_key": brand_key(brand, normative_document),
        "normalized_brand_key": normalized_brand_key(brand, normative_document),
        "raw_item": raw_item,
        "review_flags": sorted(set(flags)),
    }


class GostGsmPipeline:
    def __init__(self, project_root: str | Path):
        self.root = Path(project_root).expanduser().resolve()
        if self.root == Path(self.root.anchor) or self.root == Path.home().resolve():
            raise PipelineError(
                "Project root cannot be a filesystem or user-profile root"
            )
        self.paths = self._paths(self.root)
        self.config = self._load_config()

    @staticmethod
    def _paths(root: Path) -> dict[str, Path]:
        work = root / "work"
        manifest = work / "manifest"
        batches = work / "batches"
        staging = work / "staging"
        out = root / "out"
        return {
            "root": root,
            "marker": root / ".gost-gsm-project.json",
            "config": root / "config.json",
            "work": work,
            "manifest": manifest,
            "batches": batches,
            "staging": staging,
            "out": out,
            "source_rows": manifest / "source_rows.jsonl",
            "source_cells": manifest / "source_cells.jsonl",
            "purpose_rows": manifest / "purpose_rows.jsonl",
            "tables_catalog": manifest / "tables_catalog.json",
            "progress": work / "progress.json",
            "review_flags": work / "review_flags.jsonl",
            "qc_report": work / "qc_report.md",
            "table1": staging / "table1_occurrences.jsonl",
            "table2": staging / "table2_occurrences.jsonl",
            "table3": staging / "table3_occurrences.jsonl",
            "out1": out / "01_gsm_brands.csv",
            "out2": out / "02_gsm_group_brand.csv",
            "out3": out / "03_gsm_relations.csv",
        }

    def _load_config(self) -> dict[str, Any]:
        user_config = read_json(self.paths["config"], {})
        if user_config is None:
            user_config = {}
        if not isinstance(user_config, dict):
            raise PipelineError(
                f"Configuration must be a JSON object: {self.paths['config']}"
            )
        return deep_merge(DEFAULT_CONFIG, user_config)

    def _ensure_dirs(self) -> None:
        for name in ("root", "work", "manifest", "batches", "staging", "out"):
            self.paths[name].mkdir(parents=True, exist_ok=True)

    def _clear_generated_files(self) -> None:
        generated = [
            self.paths["source_rows"],
            self.paths["source_cells"],
            self.paths["purpose_rows"],
            self.paths["tables_catalog"],
            self.paths["progress"],
            self.paths["review_flags"],
            self.paths["qc_report"],
            self.paths["table1"],
            self.paths["table2"],
            self.paths["table3"],
            self.paths["out1"],
            self.paths["out2"],
            self.paths["out3"],
        ]
        generated.extend(self.paths["batches"].glob("B*.json"))
        for path in generated:
            if path.is_file():
                path.unlink()

    def _relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.root))
        except ValueError:
            return str(path.resolve())

    def _evaluated_tables(
        self, source_path: str | Path
    ) -> tuple[list[Path], list[dict[str, Any]]]:
        source = Path(source_path).expanduser()
        if not source.is_absolute():
            source = (self.root / source).resolve()
        else:
            source = source.resolve()
        if not source.exists():
            raise PipelineError(f"Source path does not exist: {source}")
        files = collect_source_files(source)
        if not files:
            raise PipelineError("No .html, .htm, or .md sources were found")
        tables = extract_tables(files, self.root)
        if not tables:
            raise PipelineError("No HTML or Markdown tables were found in the source")
        aliases = self.config["header_aliases"]
        for table in tables:
            table["brand_candidate"] = evaluate_table(
                table["matrix"],
                aliases,
                required=["main", "duplicate", "reserve", "foreign"],
                optional=["group", "subgroup", "ground", "aviation", "marine", "note"],
            )
            table["purpose_candidate"] = evaluate_table(
                table["matrix"],
                aliases,
                required=["brand", "purpose"],
                optional=["group", "subgroup", "normdoc", "note"],
            )
        return files, tables

    def scan_source(self, source_path: str | Path) -> dict[str, Any]:
        files, tables = self._evaluated_tables(source_path)
        return {
            "source_files": [self._relative(path) for path in files],
            "tables": [self._catalog_entry(table) for table in tables],
            "brand_candidates": [
                table["table_id"]
                for table in tables
                if table["brand_candidate"]["valid"]
            ],
            "purpose_candidates": [
                table["table_id"]
                for table in tables
                if table["purpose_candidate"]["valid"]
            ],
        }

    @staticmethod
    def _catalog_entry(table: dict[str, Any]) -> dict[str, Any]:
        def candidate(name: str) -> dict[str, Any]:
            value = table[name]
            return {
                "valid": value["valid"],
                "score": value["score"],
                "header_depth": value["header_depth"],
                "headers": value["headers"],
                "column_map": value["column_map"],
            }

        preview_limit = min(len(table["matrix"]), 5)
        return {
            "table_id": table["table_id"],
            "document_order": table["document_order"],
            "source_file": table["source_file"],
            "source_format": table["source_format"],
            "row_count": table["row_count"],
            "column_count": table["column_count"],
            "brand_candidate": candidate("brand_candidate"),
            "purpose_candidate": candidate("purpose_candidate"),
            "preview": table["matrix"][:preview_limit],
        }

    def initialize(
        self,
        source_path: str | Path,
        force: bool = False,
        brand_table_ids: list[str] | None = None,
        purpose_table_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        self._ensure_dirs()
        existing = read_json(self.paths["progress"])
        if existing and not force:
            source = Path(source_path).expanduser()
            if not source.is_absolute():
                source = (self.root / source).resolve()
            else:
                source = source.resolve()
            if existing.get("source_path") != self._relative(source):
                raise PipelineError(
                    "This job is already initialized for another source; use a different "
                    "project_root or explicitly reinitialize with force=true"
                )
            return {"status": "already_initialized", "progress": self.progress()}

        files, tables = self._evaluated_tables(source_path)
        by_id = {table["table_id"]: table for table in tables}
        brand_tables = self._select_tables(
            tables, by_id, "brand_candidate", brand_table_ids
        )
        excluded = {table["table_id"] for table in brand_tables}
        purpose_tables = self._select_tables(
            tables, by_id, "purpose_candidate", purpose_table_ids, excluded
        )
        if not purpose_tables and not purpose_table_ids:
            purpose_tables = self._select_tables(
                tables, by_id, "purpose_candidate", None
            )
        if not brand_tables:
            raise PipelineError(
                "No main brand table was detected. Call scan_source and pass brand_table_ids explicitly."
            )
        if not purpose_tables:
            raise PipelineError(
                "No purpose table was detected. Call scan_source and pass purpose_table_ids explicitly."
            )

        source_rows, source_cells = self._build_source_manifest(brand_tables)
        purpose_rows = self._build_purpose_manifest(purpose_tables)
        if not source_rows or not source_cells:
            raise PipelineError("The selected brand tables produced no brand cells")
        if not purpose_rows:
            raise PipelineError("The selected purpose tables produced no purpose rows")

        if force:
            self._clear_generated_files()

        write_jsonl(self.paths["source_rows"], source_rows)
        write_jsonl(self.paths["source_cells"], source_cells)
        write_jsonl(self.paths["purpose_rows"], purpose_rows)
        for name in ("review_flags", "table1", "table2", "table3"):
            write_jsonl(self.paths[name], [])
        self.paths["qc_report"].write_text(
            "# GSM GOST quality-control report\n\n", encoding="utf-8"
        )

        source = Path(source_path).expanduser()
        if not source.is_absolute():
            source = (self.root / source).resolve()
        catalog = {
            "generated_at": utc_now(),
            "source_path": self._relative(source),
            "source_files": [self._relative(path) for path in files],
            "selected_brand_table_ids": [table["table_id"] for table in brand_tables],
            "selected_purpose_table_ids": [
                table["table_id"] for table in purpose_tables
            ],
            "tables": [self._catalog_entry(table) for table in tables],
        }
        write_json(self.paths["tables_catalog"], catalog)
        write_json(
            self.paths["marker"],
            {"kind": "gost-gsm-pipeline", "created_at": utc_now()},
        )
        progress = {
            "status": "initialized",
            "source_path": self._relative(source),
            "brand_table_ids": catalog["selected_brand_table_ids"],
            "purpose_table_ids": catalog["selected_purpose_table_ids"],
            "total_rows": len(source_rows),
            "total_nonempty_brand_cells": len(source_cells),
            "processed_rows": 0,
            "processed_nonempty_brand_cells": 0,
            "last_completed_batch": None,
            "next_row_id": source_rows[0]["row_id"],
            "next_batch_number": 1,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        write_json(self.paths["progress"], progress)
        return {
            "status": "initialized",
            "source_files": catalog["source_files"],
            "brand_table_ids": progress["brand_table_ids"],
            "purpose_table_ids": progress["purpose_table_ids"],
            "total_rows": progress["total_rows"],
            "total_nonempty_brand_cells": progress["total_nonempty_brand_cells"],
            "purpose_rows": len(purpose_rows),
            "next_row_id": progress["next_row_id"],
            "tables_catalog": str(self.paths["tables_catalog"]),
        }

    @staticmethod
    def _select_tables(
        tables: list[dict[str, Any]],
        by_id: dict[str, dict[str, Any]],
        candidate_key: str,
        requested: list[str] | None,
        excluded: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        excluded = excluded or set()
        if requested:
            missing = [table_id for table_id in requested if table_id not in by_id]
            if missing:
                raise PipelineError(f"Unknown table IDs: {', '.join(missing)}")
            invalid = [
                table_id
                for table_id in requested
                if not by_id[table_id][candidate_key]["valid"]
            ]
            if invalid:
                raise PipelineError(
                    f"Selected tables do not have the required columns: {', '.join(invalid)}"
                )
            return sorted(
                [by_id[table_id] for table_id in requested if table_id not in excluded],
                key=lambda table: table["document_order"],
            )
        return sorted(
            [
                table
                for table in tables
                if table[candidate_key]["valid"] and table["table_id"] not in excluded
            ],
            key=lambda table: table["document_order"],
        )

    @staticmethod
    def _cell(row: list[str], index: int | None) -> str:
        return clean_text(row[index]) if index is not None and index < len(row) else ""

    def _build_source_manifest(
        self, tables: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        source_rows: list[dict[str, Any]] = []
        source_cells: list[dict[str, Any]] = []
        previous_group: str | None = None
        previous_subgroup: str | None = None
        serial = 0

        for table in tables:
            candidate = table["brand_candidate"]
            columns = candidate["column_map"]
            for physical_index, physical_row in enumerate(
                table["matrix"][candidate["header_depth"] :],
                start=candidate["header_depth"] + 1,
            ):
                raw = {
                    role: self._cell(physical_row, columns.get(role))
                    for role in BRAND_ROLES
                }
                if self._looks_like_repeated_header(raw):
                    continue
                group_raw = self._cell(physical_row, columns.get("group"))
                subgroup_raw = self._cell(physical_row, columns.get("subgroup"))
                if group_raw:
                    previous_group = inline_text(group_raw)
                if subgroup_raw:
                    previous_subgroup = inline_text(subgroup_raw)
                if not any(raw.values()):
                    continue

                serial += 1
                row_id = f"R{serial:06d}"
                equipment_raw = {
                    "ground": self._cell(physical_row, columns.get("ground")),
                    "aviation": self._cell(physical_row, columns.get("aviation")),
                    "marine": self._cell(physical_row, columns.get("marine")),
                }
                equipment = {
                    key: parse_equipment(value) for key, value in equipment_raw.items()
                }
                row_flags: list[str] = []
                for key, value in equipment_raw.items():
                    if value and equipment[key] is None:
                        row_flags.append(f"unrecognized_{key}_equipment_marker")
                nonempty_roles = [role for role, value in raw.items() if value]
                if (
                    not group_raw
                    and not subgroup_raw
                    and all(value is None for value in equipment.values())
                    and len(nonempty_roles) == 1
                    and " по " not in f" {comparison_text(raw[nonempty_roles[0]])} "
                ):
                    row_flags.append("possible_continuation_row")

                note = self._cell(physical_row, columns.get("note"))
                source_row = {
                    "row_id": row_id,
                    "row_index": serial,
                    "source_file": table["source_file"],
                    "source_table_id": table["table_id"],
                    "source_table_row_index": physical_index,
                    "group": inline_text(group_raw) if group_raw else previous_group,
                    "subgroup": inline_text(subgroup_raw)
                    if subgroup_raw
                    else previous_subgroup,
                    "ground": equipment["ground"],
                    "aviation": equipment["aviation"],
                    "marine": equipment["marine"],
                    "main_raw": raw["main"],
                    "duplicate_raw": raw["duplicate"],
                    "reserve_raw": raw["reserve"],
                    "foreign_raw": raw["foreign"],
                    "note_raw": note,
                    "row_flags": sorted(set(row_flags)),
                    "source_excerpt": inline_text(
                        " | ".join(
                            value
                            for value in [
                                group_raw,
                                subgroup_raw,
                                raw["main"],
                                raw["duplicate"],
                                raw["reserve"],
                                raw["foreign"],
                                note,
                            ]
                            if value
                        )
                    ),
                }
                source_rows.append(source_row)
                for role in BRAND_ROLES:
                    if raw[role]:
                        source_cells.append(
                            {
                                "cell_id": f"{row_id}.{role}",
                                "row_id": row_id,
                                "source_file": table["source_file"],
                                "source_table_id": table["table_id"],
                                "role": role,
                                "raw_cell": raw[role],
                            }
                        )
        return source_rows, source_cells

    @staticmethod
    def _looks_like_repeated_header(raw: dict[str, str]) -> bool:
        matches = 0
        for role, value in raw.items():
            normalized = comparison_text(value)
            if role in normalized and "марка" in normalized:
                matches += 1
            elif role == "main" and "основн" in normalized and "марка" in normalized:
                matches += 1
            elif (
                role == "duplicate" and "дублир" in normalized and "марка" in normalized
            ):
                matches += 1
            elif role == "reserve" and "резерв" in normalized and "марка" in normalized:
                matches += 1
            elif (
                role == "foreign" and "зарубеж" in normalized and "марка" in normalized
            ):
                matches += 1
        return matches >= 3

    def _build_purpose_manifest(
        self, tables: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        previous_group: str | None = None
        previous_subgroup: str | None = None
        serial = 0
        for table in tables:
            candidate = table["purpose_candidate"]
            columns = candidate["column_map"]
            for physical_index, physical_row in enumerate(
                table["matrix"][candidate["header_depth"] :],
                start=candidate["header_depth"] + 1,
            ):
                brand_raw = self._cell(physical_row, columns.get("brand"))
                purpose = inline_text(self._cell(physical_row, columns.get("purpose")))
                group_raw = self._cell(physical_row, columns.get("group"))
                subgroup_raw = self._cell(physical_row, columns.get("subgroup"))
                if group_raw:
                    previous_group = inline_text(group_raw)
                if subgroup_raw:
                    previous_subgroup = inline_text(subgroup_raw)
                if not brand_raw or not purpose:
                    continue
                hint = self._cell(physical_row, columns.get("normdoc")) or None
                note = self._cell(physical_row, columns.get("note"))
                for raw_item in split_cell_items(brand_raw, self.config):
                    parsed = parse_brand_item(raw_item, self.config, hint)
                    serial += 1
                    result.append(
                        {
                            "purpose_row_id": f"P{serial:06d}",
                            "source_file": table["source_file"],
                            "source_table_id": table["table_id"],
                            "source_table_row_index": physical_index,
                            "group": inline_text(group_raw)
                            if group_raw
                            else previous_group,
                            "subgroup": inline_text(subgroup_raw)
                            if subgroup_raw
                            else previous_subgroup,
                            "brand_raw": brand_raw,
                            "brand": parsed["brand"],
                            "normative_document": parsed["normative_document"],
                            "brand_key": parsed["brand_key"],
                            "normalized_brand_key": parsed["normalized_brand_key"],
                            "purpose_text": purpose,
                            "note_raw": note,
                            "review_flags": parsed["review_flags"],
                            "source_excerpt": inline_text(
                                " | ".join(
                                    value
                                    for value in [
                                        group_raw,
                                        subgroup_raw,
                                        brand_raw,
                                        purpose,
                                        note,
                                    ]
                                    if value
                                )
                            ),
                        }
                    )
        return result

    def progress(self) -> dict[str, Any]:
        progress = read_json(self.paths["progress"])
        if not progress:
            return {"status": "not_initialized", "project_root": str(self.root)}
        flags = read_jsonl(self.paths["review_flags"])
        result = deepcopy(progress)
        result["project_root"] = str(self.root)
        result["open_review_flags"] = len(flags)
        result["review_flag_categories"] = dict(
            Counter(flag["category"] for flag in flags)
        )
        result["remaining_rows"] = max(
            0, result["total_rows"] - result["processed_rows"]
        )
        result["remaining_nonempty_brand_cells"] = max(
            0,
            result["total_nonempty_brand_cells"]
            - result["processed_nonempty_brand_cells"],
        )
        return result

    def next_batch(self, batch_size: int | None = None) -> dict[str, Any]:
        progress = read_json(self.paths["progress"])
        if not progress:
            raise PipelineError("Project is not initialized")
        if progress.get("next_row_id") is None:
            return {"status": "completed", "progress": self.progress()}

        requested_size = batch_size or int(self.config["default_batch_size"])
        size = max(1, min(int(requested_size), int(self.config["max_batch_size"])))
        source_rows = read_jsonl(self.paths["source_rows"])
        source_cells = read_jsonl(self.paths["source_cells"])
        row_positions = {row["row_id"]: index for index, row in enumerate(source_rows)}
        next_row_id = progress["next_row_id"]
        if next_row_id not in row_positions:
            raise PipelineError(f"Progress references missing row {next_row_id}")
        start = row_positions[next_row_id]
        rows = source_rows[start : start + size]
        row_ids = [row["row_id"] for row in rows]
        batch_id = f"B{int(progress['next_batch_number']):06d}"
        row_id_set = set(row_ids)
        return {
            "status": "ready",
            "batch_id": batch_id,
            "row_ids": row_ids,
            "row_count": len(rows),
            "nonempty_brand_cells_in_batch": sum(
                cell["row_id"] in row_id_set for cell in source_cells
            ),
            "start_row_id": row_ids[0],
            "end_row_id": row_ids[-1],
            "rows_preview": [
                {
                    "row_id": row["row_id"],
                    "source_table_id": row["source_table_id"],
                    "group": row["group"],
                    "subgroup": row["subgroup"],
                    "main_raw": row["main_raw"],
                    "duplicate_raw": row["duplicate_raw"],
                    "reserve_raw": row["reserve_raw"],
                    "foreign_raw": row["foreign_raw"],
                }
                for row in rows
            ],
        }

    def _batch_path(self, batch_id: str, kind: str) -> Path:
        if not re.fullmatch(r"B\d{6}", batch_id):
            raise PipelineError(f"Invalid batch ID: {batch_id}")
        return self.paths["batches"] / f"{batch_id}.{kind}.json"

    def _read_batch(self, batch_id: str, kind: str, default: Any = None) -> Any:
        return read_json(self._batch_path(batch_id, kind), default)

    def _write_batch(self, batch_id: str, kind: str, value: Any) -> None:
        write_json(self._batch_path(batch_id, kind), value)

    def parse_batch(self, batch_id: str, row_ids: list[str]) -> dict[str, Any]:
        source_rows = read_jsonl(self.paths["source_rows"])
        row_lookup = {row["row_id"]: row for row in source_rows}
        missing = [row_id for row_id in row_ids if row_id not in row_lookup]
        if missing:
            raise PipelineError(f"Unknown row IDs: {', '.join(missing)}")
        parsed_rows: list[dict[str, Any]] = []
        item_count = 0
        for row_id in row_ids:
            row = row_lookup[row_id]
            items: list[dict[str, Any]] = []
            raw_cells = {role: row[f"{role}_raw"] for role in BRAND_ROLES}
            for role in BRAND_ROLES:
                if not raw_cells[role]:
                    continue
                for index, raw_item in enumerate(
                    split_cell_items(raw_cells[role], self.config), start=1
                ):
                    parsed = parse_brand_item(raw_item, self.config)
                    items.append(
                        {
                            "item_id": f"{row_id}.{role}.{index:02d}",
                            "row_id": row_id,
                            "cell_id": f"{row_id}.{role}",
                            "role": role,
                            "index": index,
                            **parsed,
                        }
                    )
                    item_count += 1
            parsed_rows.append(
                {
                    "row_id": row_id,
                    "source_file": row["source_file"],
                    "source_table_id": row["source_table_id"],
                    "group": row["group"],
                    "subgroup": row["subgroup"],
                    "equipment": {
                        "ground": row["ground"],
                        "aviation": row["aviation"],
                        "marine": row["marine"],
                    },
                    "note_raw": row["note_raw"],
                    "row_flags": row["row_flags"],
                    "raw_cells": raw_cells,
                    "items": items,
                }
            )
        payload = {
            "batch_id": batch_id,
            "row_ids": row_ids,
            "rows": parsed_rows,
            "generated_at": utc_now(),
        }
        self._write_batch(batch_id, "parsed", payload)
        return {
            "batch_id": batch_id,
            "row_count": len(row_ids),
            "item_count": item_count,
        }

    def map_relations(self, batch_id: str) -> dict[str, Any]:
        parsed = self._effective_parsed(batch_id)
        if not parsed:
            raise PipelineError(f"Parsed batch does not exist: {batch_id}")
        relations: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        for row in parsed["rows"]:
            items_by_role = {
                role: [item for item in row["items"] if item["role"] == role]
                for role in BRAND_ROLES
            }
            mains = items_by_role["main"]
            note = inline_text(row["note_raw"])
            for role, relation_type in RELATION_TYPES.items():
                related_items = items_by_role[role]
                if not related_items:
                    continue
                if not mains:
                    unresolved.append(
                        {
                            "row_id": row["row_id"],
                            "role": role,
                            "relation_type": relation_type,
                            "main_item_ids": [],
                            "related_item_ids": [
                                item["item_id"] for item in related_items
                            ],
                            "flag": "missing_main_item",
                            "note": note,
                        }
                    )
                    continue
                if len(mains) == 1:
                    pairs = [(mains[0], related) for related in related_items]
                    mapping_basis = "single-main"
                elif len(mains) == len(related_items):
                    pairs = list(zip(mains, related_items))
                    mapping_basis = "positional"
                else:
                    unresolved.append(
                        {
                            "row_id": row["row_id"],
                            "role": role,
                            "relation_type": relation_type,
                            "main_item_ids": [item["item_id"] for item in mains],
                            "related_item_ids": [
                                item["item_id"] for item in related_items
                            ],
                            "flag": "ambiguous_relation_mapping",
                            "note": note,
                        }
                    )
                    continue
                for main, related in pairs:
                    relations.append(
                        {
                            "row_id": row["row_id"],
                            "main_item_id": main["item_id"],
                            "main_brand": main["brand"],
                            "main_brand_key": main["brand_key"],
                            "relation_type": relation_type,
                            "related_item_id": related["item_id"],
                            "related_brand": related["brand"],
                            "related_brand_key": related["brand_key"],
                            "note": note,
                            "mapping_basis": mapping_basis,
                        }
                    )
        self._write_batch(
            batch_id,
            "relations",
            {
                "batch_id": batch_id,
                "relations": relations,
                "unresolved": unresolved,
                "generated_at": utc_now(),
            },
        )
        return {
            "batch_id": batch_id,
            "relation_count": len(relations),
            "unresolved_count": len(unresolved),
        }

    @staticmethod
    def _context_matches(candidate: dict[str, Any], row: dict[str, Any]) -> bool:
        comparisons = []
        for field in ("group", "subgroup"):
            if row.get(field) and candidate.get(field):
                comparisons.append(
                    comparison_text(row[field]) == comparison_text(candidate[field])
                )
        return all(comparisons) if comparisons else True

    @staticmethod
    def _unique_purpose_candidate(
        candidates: list[dict[str, Any]], row: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, bool]:
        contextual = [
            candidate
            for candidate in candidates
            if GostGsmPipeline._context_matches(candidate, row)
        ]
        pool = contextual or candidates
        distinct: dict[str, dict[str, Any]] = {}
        for candidate in pool:
            distinct.setdefault(comparison_text(candidate["purpose_text"]), candidate)
        if len(distinct) == 1:
            return next(iter(distinct.values())), False
        return None, len(pool) > 1

    def match_purpose(self, batch_id: str) -> dict[str, Any]:
        parsed = self._effective_parsed(batch_id)
        if not parsed:
            raise PipelineError(f"Parsed batch does not exist: {batch_id}")
        purpose_rows = read_jsonl(self.paths["purpose_rows"])
        exact_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
        brand_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for purpose_row in purpose_rows:
            exact_index[purpose_row["normalized_brand_key"]].append(purpose_row)
            brand_index[normalized_brand(purpose_row["brand"])].append(purpose_row)

        matches: list[dict[str, Any]] = []
        for row in parsed["rows"]:
            for item in row["items"]:
                exact = exact_index[item["normalized_brand_key"]]
                by_brand = brand_index[normalized_brand(item["brand"])]
                selected = None
                match_type: Literal["exact", "brand_only", "ambiguous", "not_found"]
                flags: list[str] = []
                if exact:
                    selected, ambiguous = self._unique_purpose_candidate(exact, row)
                    match_type = "ambiguous" if ambiguous else "exact"
                elif by_brand:
                    selected, ambiguous = self._unique_purpose_candidate(by_brand, row)
                    match_type = "ambiguous" if ambiguous else "brand_only"
                else:
                    ambiguous = False
                    match_type = "not_found"
                if ambiguous:
                    flags.append("ambiguous_purpose_match")
                elif selected is None:
                    flags.append("purpose_not_found")
                matches.append(
                    {
                        "item_id": item["item_id"],
                        "brand": item["brand"],
                        "normative_document": item["normative_document"],
                        "brand_key": item["brand_key"],
                        "purpose_text": selected["purpose_text"] if selected else None,
                        "match_type": match_type,
                        "purpose_row_id": selected["purpose_row_id"]
                        if selected
                        else None,
                        "purpose_source_excerpt": selected["source_excerpt"]
                        if selected
                        else None,
                        "candidate_purpose_row_ids": [
                            candidate["purpose_row_id"]
                            for candidate in (exact or by_brand)
                        ],
                        "review_flags": flags,
                    }
                )
        self._write_batch(
            batch_id,
            "purpose",
            {"batch_id": batch_id, "matches": matches, "generated_at": utc_now()},
        )
        return {
            "batch_id": batch_id,
            "match_count": len(matches),
            "exact": sum(match["match_type"] == "exact" for match in matches),
            "brand_only": sum(match["match_type"] == "brand_only" for match in matches),
            "ambiguous": sum(match["match_type"] == "ambiguous" for match in matches),
            "not_found": sum(match["match_type"] == "not_found" for match in matches),
        }

    def _overrides(self, batch_id: str) -> dict[str, Any]:
        overrides = self._read_batch(
            batch_id,
            "overrides",
            {
                "batch_id": batch_id,
                "item_overrides": {},
                "purpose_overrides": {},
                "relation_overrides": [],
                "updated_at": None,
            },
        )
        overrides.setdefault("item_overrides", {})
        overrides.setdefault("purpose_overrides", {})
        overrides.setdefault("relation_overrides", [])
        return overrides

    def _effective_parsed(self, batch_id: str) -> dict[str, Any] | None:
        parsed = self._read_batch(batch_id, "parsed")
        if not parsed:
            return parsed
        effective = deepcopy(parsed)
        overrides = self._overrides(batch_id)
        resolved_parser_flags = {
            "mixed_latin_cyrillic",
            "multiple_normdocs_in_item",
            "normdoc_not_found",
            "normdoc_without_po",
            "normdoc_conflict_with_separate_column",
            "empty_brand",
        }
        for row in effective["rows"]:
            for item in row["items"]:
                override = overrides["item_overrides"].get(item["item_id"])
                if not override:
                    continue
                if override.get("brand") is not None:
                    item["brand"] = override["brand"]
                if "normative_document" in override:
                    item["normative_document"] = override["normative_document"]
                item["brand_key"] = brand_key(item["brand"], item["normative_document"])
                item["normalized_brand_key"] = normalized_brand_key(
                    item["brand"], item["normative_document"]
                )
                item["review_flags"] = [
                    flag
                    for flag in item["review_flags"]
                    if flag not in resolved_parser_flags
                ]
                item["manual_item_override"] = override
        return effective

    @staticmethod
    def _item_and_row_lookups(
        parsed: dict[str, Any],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        items: dict[str, dict[str, Any]] = {}
        rows: dict[str, dict[str, Any]] = {}
        for row in parsed["rows"]:
            rows[row["row_id"]] = row
            for item in row["items"]:
                items[item["item_id"]] = item
        return items, rows

    def stage_batch(self, batch_id: str) -> dict[str, Any]:
        parsed = self._effective_parsed(batch_id)
        relations = self._read_batch(batch_id, "relations")
        purposes = self._read_batch(batch_id, "purpose")
        if not parsed or not relations or not purposes:
            raise PipelineError(
                f"Batch {batch_id} is missing parsed, relations, or purpose data"
            )
        overrides = self._overrides(batch_id)
        items, row_lookup = self._item_and_row_lookups(parsed)
        purpose_by_item = {match["item_id"]: match for match in purposes["matches"]}
        for item_id, override in overrides["purpose_overrides"].items():
            if item_id in items:
                purpose_by_item[item_id] = {
                    "item_id": item_id,
                    "purpose_text": override["purpose_text"],
                    "match_type": "manual_override",
                    "review_flags": [],
                    "purpose_source_excerpt": override.get("source_ref")
                    or override.get("note"),
                }

        effective_relations: dict[tuple[str, str, str], dict[str, Any]] = {}
        for relation in [*relations["relations"], *overrides["relation_overrides"]]:
            relation = deepcopy(relation)
            main_item = items.get(relation["main_item_id"])
            related_item = items.get(relation["related_item_id"])
            if main_item:
                relation["main_brand"] = main_item["brand"]
                relation["main_brand_key"] = main_item["brand_key"]
            if related_item:
                relation["related_brand"] = related_item["brand"]
                relation["related_brand_key"] = related_item["brand_key"]
            key = (
                relation["main_item_id"],
                relation["relation_type"],
                relation["related_item_id"],
            )
            effective_relations[key] = relation

        table1: list[dict[str, Any]] = []
        table2: list[dict[str, Any]] = []
        table3: list[dict[str, Any]] = []
        for row in parsed["rows"]:
            for item in row["items"]:
                purpose = purpose_by_item[item["item_id"]]
                flags = sorted(
                    set(item["review_flags"])
                    | set(purpose.get("review_flags", []))
                    | set(row["row_flags"])
                )
                provenance = {
                    "batch_id": batch_id,
                    "source_file": row["source_file"],
                    "source_table_id": row["source_table_id"],
                    "row_id": row["row_id"],
                    "item_id": item["item_id"],
                    "review_flags": flags,
                }
                table1.append(
                    {
                        **provenance,
                        "cell_id": item["cell_id"],
                        "raw_cell": row["raw_cells"][item["role"]],
                        "raw_item": item["raw_item"],
                        "brand_key": item["brand_key"],
                        "Марка ГСМ": item["brand"],
                        "Нормативный документ": item["normative_document"],
                        "Назначение и условия применения": purpose.get("purpose_text"),
                        "Наземная техника": row["equipment"]["ground"],
                        "Авиационная техника": row["equipment"]["aviation"],
                        "Морская техника": row["equipment"]["marine"],
                    }
                )
                table2.append(
                    {
                        **provenance,
                        "Группа ГСМ": row["group"],
                        "Подгруппа ГСМ": row["subgroup"],
                        "Марка ГСМ": item["brand"],
                    }
                )
        for relation in effective_relations.values():
            row = row_lookup[relation["row_id"]]
            table3.append(
                {
                    "batch_id": batch_id,
                    "source_file": row["source_file"],
                    "source_table_id": row["source_table_id"],
                    "row_id": row["row_id"],
                    "main_item_id": relation["main_item_id"],
                    "related_item_id": relation["related_item_id"],
                    "review_flags": [],
                    "Марка ГСМ основная": relation["main_brand"],
                    "Тип связи": relation["relation_type"],
                    "Марка ГСМ связанная": relation["related_brand"],
                    "Примечание": inline_text(relation.get("note")),
                }
            )
        replace_batch_records(self.paths["table1"], batch_id, table1)
        replace_batch_records(self.paths["table2"], batch_id, table2)
        replace_batch_records(self.paths["table3"], batch_id, table3)
        return {
            "batch_id": batch_id,
            "table1_occurrences": len(table1),
            "table2_occurrences": len(table2),
            "table3_occurrences": len(table3),
            "item_overrides": len(overrides["item_overrides"]),
            "purpose_overrides": len(overrides["purpose_overrides"]),
            "relation_overrides": len(overrides["relation_overrides"]),
        }

    @staticmethod
    def _flag_message(category: str) -> str:
        messages = {
            "mixed_latin_cyrillic": "В позиции смешаны латиница и кириллица.",
            "multiple_normdocs_in_item": "В одной атомарной позиции обнаружено несколько обозначений НД.",
            "normdoc_not_found": "Нормативный документ не найден.",
            "normdoc_without_po": "НД найден без явного шаблона «по ...».",
            "normdoc_from_separate_column": "НД взят из отдельного столбца.",
            "normdoc_conflict_with_separate_column": "НД в позиции конфликтует с отдельным столбцом.",
            "empty_brand": "После разбора не осталось названия марки.",
            "possible_continuation_row": "Строка похожа на ошибочно отделённое OCR-продолжение.",
            "ambiguous_relation_mapping": "Основную и связанную марки нельзя сопоставить однозначно.",
            "missing_main_item": "Связанная марка есть, основной марки в строке нет.",
            "ambiguous_purpose_match": "Найдено несколько разных текстов назначения.",
            "purpose_not_found": "Назначение и условия применения не найдены.",
        }
        if category.startswith("unrecognized_"):
            return "Маркер применимости техники не распознан."
        return messages.get(category, category)

    def _batch_flags(self, batch_id: str) -> list[dict[str, Any]]:
        parsed = self._effective_parsed(batch_id) or {"rows": []}
        relations = self._read_batch(
            batch_id, "relations", {"relations": [], "unresolved": []}
        )
        purposes = self._read_batch(batch_id, "purpose", {"matches": []})
        overrides = self._overrides(batch_id)
        purpose_overrides = set(overrides["purpose_overrides"])
        covered_related = {
            relation["related_item_id"]
            for relation in [*relations["relations"], *overrides["relation_overrides"]]
        }
        purpose_by_item = {match["item_id"]: match for match in purposes["matches"]}
        raw_flags: list[dict[str, Any]] = []

        def add(
            category: str,
            row_id: str,
            evidence: str = "",
            cell_id: str | None = None,
            item_id: str | None = None,
        ) -> None:
            raw_flags.append(
                {
                    "batch_id": batch_id,
                    "category": category,
                    "severity": "warning",
                    "message": self._flag_message(category),
                    "row_id": row_id,
                    "cell_id": cell_id,
                    "item_id": item_id,
                    "evidence": evidence,
                }
            )

        for row in parsed["rows"]:
            for category in row["row_flags"]:
                add(category, row["row_id"], row.get("note_raw", ""))
            for item in row["items"]:
                for category in item["review_flags"]:
                    add(
                        category,
                        row["row_id"],
                        item["raw_item"],
                        item["cell_id"],
                        item["item_id"],
                    )
                purpose = purpose_by_item.get(item["item_id"])
                if purpose and item["item_id"] not in purpose_overrides:
                    for category in purpose["review_flags"]:
                        add(
                            category,
                            row["row_id"],
                            item["brand"],
                            item["cell_id"],
                            item["item_id"],
                        )
        for unresolved in relations["unresolved"]:
            uncovered = [
                item_id
                for item_id in unresolved["related_item_ids"]
                if item_id not in covered_related
            ]
            if uncovered:
                add(unresolved["flag"], unresolved["row_id"], "; ".join(uncovered))

        seen: set[tuple[Any, ...]] = set()
        result: list[dict[str, Any]] = []
        for flag in raw_flags:
            key = (
                flag["category"],
                flag["row_id"],
                flag["cell_id"],
                flag["item_id"],
                flag["evidence"],
            )
            if key in seen:
                continue
            seen.add(key)
            flag["flag_id"] = f"{batch_id}:F{len(result) + 1:04d}"
            result.append(flag)
        return result

    def _refresh_review_flags(self) -> int:
        flags: list[dict[str, Any]] = []
        for path in sorted(self.paths["batches"].glob("B*.flags.json")):
            payload = read_json(path, {})
            flags.extend(payload.get("flags", []))
        write_jsonl(self.paths["review_flags"], flags)
        return len(flags)

    def qc_batch(
        self,
        batch_id: str,
        advance_progress: bool = True,
        strict_sequence: bool = True,
    ) -> dict[str, Any]:
        progress = read_json(self.paths["progress"])
        if not progress:
            raise PipelineError("Project is not initialized")
        parsed = self._effective_parsed(batch_id)
        relations = self._read_batch(
            batch_id, "relations", {"relations": [], "unresolved": []}
        )
        purposes = self._read_batch(batch_id, "purpose", {"matches": []})
        if not parsed:
            raise PipelineError(f"Parsed batch does not exist: {batch_id}")

        source_rows = read_jsonl(self.paths["source_rows"])
        source_cells = read_jsonl(self.paths["source_cells"])
        staged1 = read_jsonl(self.paths["table1"])
        staged2 = read_jsonl(self.paths["table2"])
        row_positions = {row["row_id"]: index for index, row in enumerate(source_rows)}
        row_ids = parsed["row_ids"]
        errors: list[str] = []
        if not row_ids:
            errors.append("batch has no rows")
        elif strict_sequence:
            if row_ids[0] != progress["next_row_id"]:
                errors.append(
                    f"batch starts at {row_ids[0]}, expected {progress['next_row_id']}"
                )
            if row_ids[0] in row_positions:
                start = row_positions[row_ids[0]]
                expected = [
                    row["row_id"] for row in source_rows[start : start + len(row_ids)]
                ]
                if row_ids != expected:
                    errors.append("batch rows are not a contiguous source range")

        parsed_rows = {row["row_id"]: row for row in parsed["rows"]}
        for row_id in row_ids:
            if row_id not in parsed_rows:
                errors.append(f"row {row_id} is missing from parsed output")
        items = [item for row in parsed["rows"] for item in row["items"]]
        item_counts_by_cell = Counter(item["cell_id"] for item in items)
        for cell in source_cells:
            if (
                cell["row_id"] in set(row_ids)
                and not item_counts_by_cell[cell["cell_id"]]
            ):
                errors.append(f"source cell {cell['cell_id']} produced no items")

        staged1_ids = {record["item_id"] for record in staged1}
        staged2_ids = {record["item_id"] for record in staged2}
        for item in items:
            if item["item_id"] not in staged1_ids:
                errors.append(f"item {item['item_id']} is missing from Table 1 staging")
            if item["item_id"] not in staged2_ids:
                errors.append(f"item {item['item_id']} is missing from Table 2 staging")

        overrides = self._overrides(batch_id)
        resolved_related = {
            relation["related_item_id"]
            for relation in [*relations["relations"], *overrides["relation_overrides"]]
        }
        unresolved_related = {
            item_id
            for unresolved in relations["unresolved"]
            for item_id in unresolved["related_item_ids"]
        }
        for item in items:
            if (
                item["role"] != "main"
                and item["item_id"] not in resolved_related
                and item["item_id"] not in unresolved_related
            ):
                errors.append(f"non-main item {item['item_id']} has no relation status")
        if len(purposes["matches"]) != len(items):
            errors.append("purpose result count does not equal parsed item count")

        flags = self._batch_flags(batch_id)
        self._write_batch(
            batch_id,
            "flags",
            {"batch_id": batch_id, "generated_at": utc_now(), "flags": flags},
        )
        self._refresh_review_flags()
        qc_status = "failed" if errors else ("passed_with_flags" if flags else "passed")

        if advance_progress and qc_status != "failed":
            last_position = row_positions[row_ids[-1]]
            progress["processed_rows"] = last_position + 1
            completed_ids = {row["row_id"] for row in source_rows[: last_position + 1]}
            progress["processed_nonempty_brand_cells"] = sum(
                cell["row_id"] in completed_ids for cell in source_cells
            )
            progress["last_completed_batch"] = batch_id
            progress["next_row_id"] = (
                source_rows[last_position + 1]["row_id"]
                if last_position + 1 < len(source_rows)
                else None
            )
            progress["next_batch_number"] = int(progress["next_batch_number"]) + 1
            progress["status"] = (
                "completed" if progress["next_row_id"] is None else "in_progress"
            )
            progress["last_qc_status"] = qc_status
            progress["updated_at"] = utc_now()
            write_json(self.paths["progress"], progress)

        summary = {
            "batch_id": batch_id,
            "start_row_id": row_ids[0] if row_ids else None,
            "end_row_id": row_ids[-1] if row_ids else None,
            "row_count": len(row_ids),
            "item_count": len(items),
            "relation_count": len(relations["relations"])
            + len(overrides["relation_overrides"]),
            "qc_status": qc_status,
            "errors": errors,
            "open_flags": len(flags),
            "open_flag_categories": dict(Counter(flag["category"] for flag in flags)),
            "progress": self.progress(),
        }
        with self.paths["qc_report"].open(
            "a", encoding="utf-8", newline="\n"
        ) as stream:
            stream.write(f"## {batch_id}\n\n")
            stream.write(
                f"- Rows: {summary['start_row_id']} … {summary['end_row_id']}\n"
            )
            stream.write(f"- Items: {summary['item_count']}\n")
            stream.write(f"- Relations: {summary['relation_count']}\n")
            stream.write(f"- Open flags: {summary['open_flags']}\n")
            stream.write(f"- Status: {qc_status}\n")
            if errors:
                stream.write(f"- Errors: {'; '.join(errors)}\n")
            stream.write("\n")
        return summary

    def process_next_batch(self, batch_size: int | None = None) -> dict[str, Any]:
        batch = self.next_batch(batch_size)
        if batch["status"] == "completed":
            return batch
        batch_id = batch["batch_id"]
        parsed = self.parse_batch(batch_id, batch["row_ids"])
        relations = self.map_relations(batch_id)
        purpose = self.match_purpose(batch_id)
        staging = self.stage_batch(batch_id)
        qc = self.qc_batch(batch_id)
        return {
            "status": qc["qc_status"],
            "batch_id": batch_id,
            "range": [batch["start_row_id"], batch["end_row_id"]],
            "nonempty_brand_cells_in_batch": batch["nonempty_brand_cells_in_batch"],
            "parse": parsed,
            "relations": relations,
            "purpose": purpose,
            "staging": staging,
            "qc": qc,
        }

    def process_batches(
        self,
        batch_size: int | None = None,
        max_batches: int = 1,
        stop_on_flags: bool = False,
    ) -> dict[str, Any]:
        max_batches = max(1, min(int(max_batches), 1000))
        summaries: list[dict[str, Any]] = []
        for _ in range(max_batches):
            result = self.process_next_batch(batch_size)
            if result.get("status") == "completed":
                break
            summaries.append(
                {
                    "batch_id": result["batch_id"],
                    "range": result["range"],
                    "items": result["parse"]["item_count"],
                    "relations": result["relations"]["relation_count"],
                    "flags": result["qc"]["open_flags"],
                    "qc_status": result["qc"]["qc_status"],
                }
            )
            if result["qc"]["qc_status"] == "failed":
                break
            if stop_on_flags and result["qc"]["open_flags"]:
                break
            if result["qc"]["progress"]["status"] == "completed":
                break
        return {
            "batches_processed": len(summaries),
            "batches": summaries,
            "progress": self.progress(),
        }

    def list_review_flags(
        self,
        limit: int = 50,
        categories: list[str] | None = None,
        row_id: str | None = None,
    ) -> dict[str, Any]:
        flags = read_jsonl(self.paths["review_flags"])
        if categories:
            allowed = set(categories)
            flags = [flag for flag in flags if flag["category"] in allowed]
        if row_id:
            flags = [flag for flag in flags if flag["row_id"] == row_id]
        limit = max(1, min(int(limit), 500))
        return {
            "total_matching": len(flags),
            "categories": dict(Counter(flag["category"] for flag in flags)),
            "items": flags[:limit],
        }

    def row_detail(self, row_id: str) -> dict[str, Any]:
        source = next(
            (
                row
                for row in read_jsonl(self.paths["source_rows"])
                if row["row_id"] == row_id
            ),
            None,
        )
        if source is None:
            raise PipelineError(f"Unknown row ID: {row_id}")
        result: dict[str, Any] = {
            "source_row": source,
            "batch_id": None,
            "parsed_row": None,
            "relations": {"resolved": [], "unresolved": []},
            "purpose_matches": [],
            "overrides": None,
            "review_flags": self.list_review_flags(limit=500, row_id=row_id)["items"],
        }
        for path in sorted(self.paths["batches"].glob("B*.parsed.json")):
            parsed = read_json(path, {})
            original_parsed_row = next(
                (row for row in parsed.get("rows", []) if row["row_id"] == row_id), None
            )
            if original_parsed_row is None:
                continue
            batch_id = parsed["batch_id"]
            effective = self._effective_parsed(batch_id)
            parsed_row = next(
                row for row in effective["rows"] if row["row_id"] == row_id
            )
            relations = self._read_batch(
                batch_id, "relations", {"relations": [], "unresolved": []}
            )
            purposes = self._read_batch(batch_id, "purpose", {"matches": []})
            item_ids = {item["item_id"] for item in parsed_row["items"]}
            result.update(
                {
                    "batch_id": batch_id,
                    "original_parsed_row": original_parsed_row,
                    "parsed_row": parsed_row,
                    "relations": {
                        "resolved": [
                            relation
                            for relation in relations["relations"]
                            if relation["row_id"] == row_id
                        ],
                        "unresolved": [
                            unresolved
                            for unresolved in relations["unresolved"]
                            if unresolved["row_id"] == row_id
                        ],
                    },
                    "purpose_matches": [
                        match
                        for match in purposes["matches"]
                        if match["item_id"] in item_ids
                    ],
                    "overrides": self._overrides(batch_id),
                }
            )
            break
        return result

    def find_purpose_candidates(
        self,
        item_id: str | None = None,
        brand: str | None = None,
        normative_document: str | None = None,
        group: str | None = None,
        subgroup: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        if item_id:
            row_id = item_id.split(".", 1)[0]
            detail = self.row_detail(row_id)
            parsed_row = detail["parsed_row"]
            if parsed_row is None:
                raise PipelineError(f"Item has not been parsed: {item_id}")
            item = next(
                (item for item in parsed_row["items"] if item["item_id"] == item_id),
                None,
            )
            if item is None:
                raise PipelineError(f"Unknown item ID: {item_id}")
            brand = item["brand"]
            normative_document = item["normative_document"]
            group = parsed_row["group"]
            subgroup = parsed_row["subgroup"]
        if not brand:
            raise PipelineError("Provide item_id or brand")

        target_key = normalized_brand_key(brand, normative_document)
        target_brand = normalized_brand(brand)
        candidates: list[dict[str, Any]] = []
        for row in read_jsonl(self.paths["purpose_rows"]):
            score = 0
            reasons: list[str] = []
            if normative_document and row["normalized_brand_key"] == target_key:
                score += 100
                reasons.append("brand_key")
            elif normalized_brand(row["brand"]) == target_brand:
                score += 70
                reasons.append("brand")
            elif target_brand and target_brand in normalized_brand(row["brand"]):
                score += 30
                reasons.append("brand_substring")
            else:
                continue
            if (
                group
                and row["group"]
                and comparison_text(group) == comparison_text(row["group"])
            ):
                score += 10
                reasons.append("group")
            if (
                subgroup
                and row["subgroup"]
                and comparison_text(subgroup) == comparison_text(row["subgroup"])
            ):
                score += 10
                reasons.append("subgroup")
            candidates.append(
                {
                    "score": score,
                    "reasons": reasons,
                    "purpose_row_id": row["purpose_row_id"],
                    "source_file": row["source_file"],
                    "source_table_id": row["source_table_id"],
                    "group": row["group"],
                    "subgroup": row["subgroup"],
                    "brand": row["brand"],
                    "normative_document": row["normative_document"],
                    "purpose_text": row["purpose_text"],
                    "source_excerpt": row["source_excerpt"],
                }
            )
        candidates.sort(
            key=lambda candidate: (-candidate["score"], candidate["purpose_row_id"])
        )
        return {
            "brand": brand,
            "normative_document": normative_document,
            "group": group,
            "subgroup": subgroup,
            "candidates": candidates[: max(1, min(int(limit), 100))],
        }

    def set_item_override(
        self,
        batch_id: str,
        item_id: str,
        brand: str | None = None,
        normative_document: str | None = None,
        clear_normative_document: bool = False,
        source_ref: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        parsed = self._read_batch(batch_id, "parsed")
        if not parsed:
            raise PipelineError(f"Parsed batch does not exist: {batch_id}")
        items, _ = self._item_and_row_lookups(parsed)
        item = items.get(item_id)
        if item is None:
            raise PipelineError(f"Item {item_id} does not belong to {batch_id}")
        if (
            brand is None
            and normative_document is None
            and not clear_normative_document
        ):
            raise PipelineError(
                "Provide brand, normative_document, or clear_normative_document=true"
            )
        new_brand = inline_text(brand) if brand is not None else item["brand"]
        if not new_brand:
            raise PipelineError("Brand override cannot be empty")
        if clear_normative_document:
            new_document = None
        elif normative_document is not None:
            new_document = inline_text(normative_document) or None
        else:
            new_document = item["normative_document"]
        overrides = self._overrides(batch_id)
        overrides["item_overrides"][item_id] = {
            "item_id": item_id,
            "brand": new_brand,
            "normative_document": new_document,
            "source_ref": source_ref,
            "note": note,
        }
        overrides["updated_at"] = utc_now()
        self._write_batch(batch_id, "overrides", overrides)
        relations = self.map_relations(batch_id)
        purpose = self.match_purpose(batch_id)
        stage = self.stage_batch(batch_id)
        qc = self.qc_batch(batch_id, advance_progress=False, strict_sequence=False)
        return {
            "status": "override_saved",
            "relations": relations,
            "purpose": purpose,
            "stage": stage,
            "qc": qc,
        }

    def set_purpose_override(
        self,
        batch_id: str,
        item_id: str,
        purpose_row_id: str | None = None,
        purpose_text: str | None = None,
        source_ref: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        parsed = self._effective_parsed(batch_id)
        if not parsed:
            raise PipelineError(f"Parsed batch does not exist: {batch_id}")
        items, _ = self._item_and_row_lookups(parsed)
        if item_id not in items:
            raise PipelineError(f"Item {item_id} does not belong to {batch_id}")
        if purpose_row_id:
            purpose_row = next(
                (
                    row
                    for row in read_jsonl(self.paths["purpose_rows"])
                    if row["purpose_row_id"] == purpose_row_id
                ),
                None,
            )
            if purpose_row is None:
                raise PipelineError(f"Unknown purpose row: {purpose_row_id}")
            purpose_text = purpose_row["purpose_text"]
            source_ref = source_ref or purpose_row["source_excerpt"]
        if not inline_text(purpose_text):
            raise PipelineError("purpose_text or purpose_row_id is required")
        overrides = self._overrides(batch_id)
        overrides["purpose_overrides"][item_id] = {
            "item_id": item_id,
            "purpose_row_id": purpose_row_id,
            "purpose_text": inline_text(purpose_text),
            "source_ref": source_ref,
            "note": note,
        }
        overrides["updated_at"] = utc_now()
        self._write_batch(batch_id, "overrides", overrides)
        stage = self.stage_batch(batch_id)
        qc = self.qc_batch(batch_id, advance_progress=False, strict_sequence=False)
        return {"status": "override_saved", "stage": stage, "qc": qc}

    def add_relation_override(
        self,
        batch_id: str,
        main_item_id: str,
        related_item_id: str,
        relation_type: str,
        note: str = "",
    ) -> dict[str, Any]:
        if relation_type not in VALID_RELATION_TYPES:
            raise PipelineError(
                f"relation_type must be one of: {', '.join(sorted(VALID_RELATION_TYPES))}"
            )
        parsed = self._effective_parsed(batch_id)
        if not parsed:
            raise PipelineError(f"Parsed batch does not exist: {batch_id}")
        items, rows = self._item_and_row_lookups(parsed)
        main = items.get(main_item_id)
        related = items.get(related_item_id)
        if main is None or related is None:
            raise PipelineError("main_item_id or related_item_id is not in this batch")
        if main["role"] != "main":
            raise PipelineError(
                "main_item_id must reference an item from the main column"
            )
        if related["role"] == "main":
            raise PipelineError("related_item_id cannot reference the main column")
        if main["row_id"] != related["row_id"]:
            raise PipelineError("Relation overrides must stay inside one source row")
        expected_type = RELATION_TYPES[related["role"]]
        if relation_type != expected_type:
            raise PipelineError(
                f"Column {related['role']} requires relation type {expected_type}"
            )
        row = rows[main["row_id"]]
        relation = {
            "row_id": main["row_id"],
            "main_item_id": main_item_id,
            "main_brand": main["brand"],
            "main_brand_key": main["brand_key"],
            "relation_type": relation_type,
            "related_item_id": related_item_id,
            "related_brand": related["brand"],
            "related_brand_key": related["brand_key"],
            "note": inline_text(note) or inline_text(row["note_raw"]),
            "mapping_basis": "manual",
        }
        overrides = self._overrides(batch_id)
        key = (main_item_id, relation_type, related_item_id)
        kept = [
            current
            for current in overrides["relation_overrides"]
            if (
                current["main_item_id"],
                current["relation_type"],
                current["related_item_id"],
            )
            != key
        ]
        kept.append(relation)
        overrides["relation_overrides"] = kept
        overrides["updated_at"] = utc_now()
        self._write_batch(batch_id, "overrides", overrides)
        stage = self.stage_batch(batch_id)
        qc = self.qc_batch(batch_id, advance_progress=False, strict_sequence=False)
        return {"status": "override_saved", "stage": stage, "qc": qc}

    def clear_override(
        self,
        batch_id: str,
        item_id: str | None = None,
        main_item_id: str | None = None,
        related_item_id: str | None = None,
        relation_type: str | None = None,
        clear_item_value: bool = False,
    ) -> dict[str, Any]:
        overrides = self._overrides(batch_id)
        removed = 0
        if clear_item_value and item_id and item_id in overrides["item_overrides"]:
            del overrides["item_overrides"][item_id]
            removed += 1
        if item_id and item_id in overrides["purpose_overrides"]:
            del overrides["purpose_overrides"][item_id]
            removed += 1
        if main_item_id or related_item_id or relation_type:
            kept = []
            for relation in overrides["relation_overrides"]:
                matches = (
                    (not main_item_id or relation["main_item_id"] == main_item_id)
                    and (
                        not related_item_id
                        or relation["related_item_id"] == related_item_id
                    )
                    and (
                        not relation_type or relation["relation_type"] == relation_type
                    )
                )
                if matches:
                    removed += 1
                else:
                    kept.append(relation)
            overrides["relation_overrides"] = kept
        overrides["updated_at"] = utc_now()
        self._write_batch(batch_id, "overrides", overrides)
        if clear_item_value:
            self.map_relations(batch_id)
            self.match_purpose(batch_id)
        stage = self.stage_batch(batch_id)
        qc = self.qc_batch(batch_id, advance_progress=False, strict_sequence=False)
        return {
            "status": "override_cleared",
            "removed": removed,
            "stage": stage,
            "qc": qc,
        }

    def validate_final(self, allow_partial: bool = False) -> dict[str, Any]:
        progress = self.progress()
        blockers: list[dict[str, Any]] = []
        if progress["status"] != "completed" and not allow_partial:
            blockers.append(
                {
                    "category": "incomplete_processing",
                    "message": f"{progress.get('remaining_rows', '?')} source rows remain",
                }
            )
        blocking_categories = {
            "ambiguous_relation_mapping",
            "missing_main_item",
            "ambiguous_purpose_match",
        }
        review_flags = read_jsonl(self.paths["review_flags"])
        blockers.extend(
            {
                "category": flag["category"],
                "message": flag["message"],
                "row_id": flag["row_id"],
                "item_id": flag["item_id"],
            }
            for flag in review_flags
            if flag["category"] in blocking_categories
        )

        table1 = read_jsonl(self.paths["table1"])
        table2 = read_jsonl(self.paths["table2"])
        source_items = {record["item_id"] for record in table1}
        if source_items != {record["item_id"] for record in table2}:
            blockers.append(
                {
                    "category": "staging_coverage_mismatch",
                    "message": "Table 1 and Table 2 do not cover the same items",
                }
            )
        by_brand_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in table1:
            by_brand_key[record["brand_key"]].append(record)
        for key, records in by_brand_key.items():
            purposes = {
                comparison_text(record["Назначение и условия применения"])
                for record in records
                if record["Назначение и условия применения"]
            }
            if len(purposes) > 1:
                blockers.append(
                    {
                        "category": "conflicting_purpose_values",
                        "message": f"Brand key has {len(purposes)} different purpose texts",
                        "brand_key": key,
                        "item_ids": [record["item_id"] for record in records],
                    }
                )
        return {
            "valid": not blockers,
            "blocker_count": len(blockers),
            "blockers": blockers[:500],
            "progress": progress,
            "staging_counts": {
                "table1": len(table1),
                "table2": len(table2),
                "table3": len(read_jsonl(self.paths["table3"])),
            },
        }

    @staticmethod
    def _deduplicate(rows: list[list[str]]) -> list[list[str]]:
        result: list[list[str]] = []
        seen: set[tuple[str, ...]] = set()
        for row in rows:
            key = tuple(row)
            if key not in seen:
                seen.add(key)
                result.append(row)
        return result

    @staticmethod
    def _aggregate_equipment(records: list[dict[str, Any]], field: str) -> bool | None:
        values = [record[field] for record in records if record[field] is not None]
        if any(value is True for value in values):
            return True
        if values:
            return False
        return None

    def export_final_csv(
        self,
        allow_partial: bool = False,
        allow_blockers: bool = False,
    ) -> dict[str, Any]:
        validation = self.validate_final(allow_partial=allow_partial)
        if not validation["valid"] and not allow_blockers:
            raise PipelineError(
                "Final validation has blockers; inspect validate_final or explicitly set allow_blockers=true"
            )
        table1 = read_jsonl(self.paths["table1"])
        table2 = read_jsonl(self.paths["table2"])
        table3 = read_jsonl(self.paths["table3"])

        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in table1:
            grouped.setdefault(record["brand_key"], []).append(record)
        out1: list[list[str]] = []
        for records in grouped.values():
            first = records[0]
            purpose = next(
                (
                    record["Назначение и условия применения"]
                    for record in records
                    if record["Назначение и условия применения"]
                ),
                "",
            )
            out1.append(
                [
                    first["Марка ГСМ"] or "",
                    first["Нормативный документ"] or "",
                    purpose or "",
                    equipment_export(
                        self._aggregate_equipment(records, "Наземная техника")
                    ),
                    equipment_export(
                        self._aggregate_equipment(records, "Авиационная техника")
                    ),
                    equipment_export(
                        self._aggregate_equipment(records, "Морская техника")
                    ),
                ]
            )
        out2 = self._deduplicate(
            [
                [
                    record["Группа ГСМ"] or "",
                    record["Подгруппа ГСМ"] or "",
                    record["Марка ГСМ"] or "",
                ]
                for record in table2
            ]
        )
        out3 = self._deduplicate(
            [
                [
                    record["Марка ГСМ основная"] or "",
                    record["Тип связи"] or "",
                    record["Марка ГСМ связанная"] or "",
                    record["Примечание"] or "",
                ]
                for record in table3
            ]
        )
        headers = [
            (
                self.paths["out1"],
                [
                    "Марка ГСМ",
                    "Нормативный документ",
                    "Назначение и условия применения",
                    "Наземная техника",
                    "Авиационная техника",
                    "Морская техника",
                ],
                out1,
            ),
            (
                self.paths["out2"],
                ["Группа ГСМ", "Подгруппа ГСМ", "Марка ГСМ"],
                out2,
            ),
            (
                self.paths["out3"],
                [
                    "Марка ГСМ основная",
                    "Тип связи",
                    "Марка ГСМ связанная",
                    "Примечание",
                ],
                out3,
            ),
        ]
        self.paths["out"].mkdir(parents=True, exist_ok=True)
        for path, header, rows in headers:
            temporary = path.with_name(f".{path.name}.tmp")
            try:
                with temporary.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.writer(stream, delimiter=";", lineterminator="\n")
                    writer.writerow(header)
                    writer.writerows(rows)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return {
            "status": "exported",
            "validation": validation,
            "files": [str(path) for path, _, _ in headers],
            "row_counts": {
                self.paths["out1"].name: len(out1),
                self.paths["out2"].name: len(out2),
                self.paths["out3"].name: len(out3),
            },
        }
