"""CSV-backed normalization of fuel/lubricant names and their relations.

The normalizer deliberately has no database or web-framework dependencies.  Its
smallest public operation is ``normalize_text``: a string goes in and a strict
``brand по document; ...`` string comes out.  ``analyze`` exposes the same result
with candidates for the Digitizer UI.
"""

from __future__ import annotations

import csv
import io
import os
import re
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


BRAND_HEADER = "Марка ГСМ"
DOCUMENT_HEADER = "Нормативный документ"
RELATION_HEADERS = ("Первая марка", "Тип связи", "Вторая марка")
COLUMN_TYPES = {
    "other": "другой",
    "primary": "основная марка ГСМ",
    "duplicate": "дублирующая марка ГСМ",
    "reserve": "резервная марка ГСМ",
    "foreign": "зарубежная марка ГСМ",
    "duplicate_reserve": "дублирующая и резервная одновременно",
}
RELATION_TYPES = {
    "duplicate": ("дублирующая",),
    "reserve": ("резервная",),
    "foreign": ("зарубежная",),
    "duplicate_reserve": ("дублирующая", "резервная"),
}

_SPLIT_RE = re.compile(r"[;,\r\n]+")
_SPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^0-9A-ZА-Я]+")
_DOCUMENT_START_RE = re.compile(
    r"(?:^|\s)(?:ГОСТ|ОСТ|ТУ|СТО|ASTM|ISO|DIN|SAE|API)(?:\s|$)"
)
_CONFUSABLES = str.maketrans(
    {
        "A": "А",
        "B": "В",
        "C": "С",
        "E": "Е",
        "H": "Н",
        "K": "К",
        "M": "М",
        "O": "О",
        "P": "Р",
        "T": "Т",
        "X": "Х",
        "Y": "У",
        "I": "1",
        "L": "1",
        "|": "1",
    }
)


@dataclass(frozen=True)
class CatalogEntry:
    brand: str
    document: str

    @property
    def value(self) -> str:
        return f"{self.brand} по {self.document}"

    def public(self) -> dict[str, str]:
        return {"brand": self.brand, "document": self.document, "value": self.value}


def _match_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value)).upper().replace("Ё", "Е")
    value = value.replace("–", "-").replace("—", "-").replace("−", "-")
    return _SPACE_RE.sub(" ", value.translate(_CONFUSABLES)).strip()


def _compact(value: str) -> str:
    return _NON_WORD_RE.sub("", _match_text(value))


def _similarity(fragment: str, expected: str) -> float:
    query, target = _compact(fragment), _compact(expected)
    if not query or not target:
        return 0.0
    if target == query:
        return 1.0
    if target in query:
        return 0.9
    lengths = range(max(1, len(target) - 2), min(len(query), len(target) + 2) + 1)
    windows = (
        query[start : start + length]
        for length in lengths
        for start in range(0, len(query) - length + 1)
    )
    return max(
        [SequenceMatcher(None, query, target).ratio()]
        + [SequenceMatcher(None, window, target).ratio() for window in windows]
    )


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Не удалось определить кодировку CSV: {path}")


def _dict_rows(path: Path) -> list[dict[str, str]]:
    text = _read_text(path)
    try:
        delimiter = csv.Sniffer().sniff(text[:8192], delimiters=";,\t,").delimiter
    except csv.Error:
        delimiter = ";"
    return [
        dict(row)
        for row in csv.DictReader(io.StringIO(text), delimiter=delimiter)
    ]


class GsmNormalizer:
    """Normalize OCR strings against a strict brand/document CSV catalog."""

    def __init__(self, catalog_path: Path) -> None:
        self.catalog_path = Path(catalog_path).expanduser().resolve()
        if not self.catalog_path.is_file():
            raise ValueError(f"Справочник марок не найден: {self.catalog_path}")
        rows = _dict_rows(self.catalog_path)
        entries: list[CatalogEntry] = []
        seen: set[tuple[str, str]] = set()
        for row_number, row in enumerate(rows, 2):
            keys = {str(key).strip().casefold(): key for key in row}
            brand_key = keys.get(BRAND_HEADER.casefold())
            document_key = keys.get(DOCUMENT_HEADER.casefold())
            if brand_key is None or document_key is None:
                raise ValueError(
                    f"{self.catalog_path}: нужны столбцы "
                    f"«{BRAND_HEADER}» и «{DOCUMENT_HEADER}»"
                )
            brand = str(row.get(brand_key) or "").strip()
            document = str(row.get(document_key) or "").strip()
            if not brand and not document:
                continue
            if not brand or not document:
                raise ValueError(
                    f"{self.catalog_path}: пустая ячейка в строке {row_number}"
                )
            identity = (_match_text(brand), _match_text(document))
            if identity not in seen:
                seen.add(identity)
                entries.append(CatalogEntry(brand, document))
        if not entries:
            raise ValueError(f"Справочник марок пуст: {self.catalog_path}")
        self.entries = tuple(entries)
        self.documents = tuple(dict.fromkeys(entry.document for entry in entries))
        self._pairs = {
            (_match_text(entry.brand), _match_text(entry.document)): entry
            for entry in entries
        }

    def _documents_in(self, text: str) -> list[tuple[str, float]]:
        normalized_text = _match_text(text)
        scored = sorted(
            (
                (
                    document,
                    1.0
                    if _match_text(document) in normalized_text
                    else _similarity(text, document),
                )
                for document in self.documents
            ),
            key=lambda item: (-item[1], item[0]),
        )
        if not scored or scored[0][1] < 0.78:
            return []
        best = scored[0][1]
        return [item for item in scored if item[1] >= max(0.78, best - 0.035)]

    def _options(self, documents: Iterable[str]) -> list[CatalogEntry]:
        allowed = {_match_text(document) for document in documents}
        options = [
            entry
            for entry in self.entries
            if not allowed or _match_text(entry.document) in allowed
        ]
        return options or list(self.entries)

    @staticmethod
    def _brand_part(fragment: str, documents: Iterable[str]) -> str:
        value = _match_text(fragment)
        for document in documents:
            value = value.replace(_match_text(document), " ")
        marker = _DOCUMENT_START_RE.search(value)
        if marker:
            value = value[: marker.start()]
        value = re.sub(r"\s+ПО\s*$", "", value).strip(" -")
        return value

    def _analyze_fragment(
        self, fragment: str, global_documents: list[str]
    ) -> dict[str, Any] | None:
        local_document_scores = self._documents_in(fragment)
        local_documents = [item[0] for item in local_document_scores]
        documents = local_documents or global_documents
        options = self._options(documents)
        brand_part = self._brand_part(fragment, documents)

        # A separator can leave the shared normative document in its own fragment.
        if local_document_scores and not _compact(brand_part):
            return None

        scored = sorted(
            ((entry, _similarity(brand_part, entry.brand)) for entry in options),
            key=lambda item: (-item[1], item[0].brand, item[0].document),
        )
        best_score = scored[0][1] if scored else 0.0
        plausible = [
            item for item in scored if item[1] >= max(0.62, best_score - 0.055)
        ]
        best_entries = [item[0] for item in plausible]
        resolved = (
            best_score >= 0.76
            and len(best_entries) == 1
        )
        if resolved:
            selected = best_entries[0]
            candidates = options
            value = selected.value
            document = selected.document
        else:
            selected = None
            candidates = options
            document = documents[0] if len(documents) == 1 else "-"
            value = f"- по {document}"
        return {
            "source": fragment.strip(),
            "brand": selected.brand if selected else "-",
            "document": document,
            "value": value,
            "resolved": bool(selected),
            "candidates": [entry.public() for entry in candidates],
        }

    def analyze(self, value: str) -> dict[str, Any]:
        """Return normalized text plus per-name choices for unresolved fragments."""
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("Нечего нормализовать")
        fragments = [part.strip() for part in _SPLIT_RE.split(raw) if part.strip()]
        global_document_scores = self._documents_in(raw)
        global_documents = [item[0] for item in global_document_scores]
        if len(global_documents) != 1:
            global_documents = []
        items = [
            item
            for fragment in fragments
            if (item := self._analyze_fragment(fragment, global_documents)) is not None
        ]
        if not items:
            # A document without a recognized brand is itself an unresolved item.
            document = global_documents[0] if len(global_documents) == 1 else "-"
            items = [
                {
                    "source": raw,
                    "brand": "-",
                    "document": document,
                    "value": f"- по {document}",
                    "resolved": False,
                    "candidates": [
                        entry.public() for entry in self._options(global_documents)
                    ],
                }
            ]
        result_items = items
        return {
            "text": "; ".join(item["value"] for item in result_items),
            "items": result_items,
            "resolved": all(item["resolved"] for item in result_items),
        }

    def normalize_text(self, value: str) -> str:
        """Simple string-in/string-out API used outside the web application."""
        return str(self.analyze(value)["text"])

    def parse_strict(self, value: str) -> list[CatalogEntry]:
        """Validate a normalized cell and return its canonical catalog entries."""
        text = str(value or "").strip()
        if not text:
            return []
        result: list[CatalogEntry] = []
        for part in (item.strip() for item in text.split(";") if item.strip()):
            pieces = re.split(r"\s+по\s+", part, maxsplit=1, flags=re.IGNORECASE)
            if len(pieces) != 2 or pieces[0].strip() == "-":
                raise ValueError(f"не выбрана марка в «{part}»")
            entry = self._pairs.get(
                (_match_text(pieces[0].strip()), _match_text(pieces[1].strip()))
            )
            if entry is None:
                raise ValueError(f"нет в справочнике: «{part}»")
            result.append(entry)
        return result


class RelationshipStore:
    """Append normalized three-column relationships to a local CSV atomically."""

    def __init__(self, path: Path, normalizer: GsmNormalizer) -> None:
        self.path = Path(path).expanduser().resolve()
        self.normalizer = normalizer
        self._lock = threading.RLock()
        if self.path.exists() and not self.path.is_file():
            raise ValueError(f"Путь CSV связей не является файлом: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _existing(self) -> list[tuple[str, str, str]]:
        if not self.path.is_file() or not self.path.stat().st_size:
            return []
        rows = _dict_rows(self.path)
        result: list[tuple[str, str, str]] = []
        for row in rows:
            keys = {str(key).strip().casefold(): key for key in row}
            if any(header.casefold() not in keys for header in RELATION_HEADERS):
                raise ValueError(
                    f"{self.path}: нужны столбцы " + ", ".join(RELATION_HEADERS)
                )
            values = tuple(
                str(row.get(keys[header.casefold()]) or "").strip()
                for header in RELATION_HEADERS
            )
            if any(values):
                result.append(values)  # type: ignore[arg-type]
        return result

    def build(
        self, rows: list[list[str]], column_types: list[str]
    ) -> list[tuple[str, str, str]]:
        if not isinstance(rows, list) or not rows:
            raise ValueError("В блоке нет строк")
        if not isinstance(column_types, list) or not column_types:
            raise ValueError("Выберите типы столбцов")
        if any(value not in COLUMN_TYPES for value in column_types):
            raise ValueError("Всем столбцам нужно выбрать тип")
        primary_indexes = [
            i for i, value in enumerate(column_types) if value == "primary"
        ]
        if len(primary_indexes) != 1:
            raise ValueError("Нужен ровно один столбец «основная марка ГСМ»")
        relation_indexes = [
            i for i, value in enumerate(column_types) if value in RELATION_TYPES
        ]
        if not relation_indexes:
            raise ValueError("Нужен минимум один столбец связанных марок")
        primary_index = primary_indexes[0]
        result: list[tuple[str, str, str]] = []
        for row_index, row in enumerate(rows, 1):
            if len(row) != len(column_types):
                raise ValueError(
                    f"Строка {row_index}: число ячеек не равно числу типов"
                )
            try:
                primary = self.normalizer.parse_strict(row[primary_index])
            except ValueError as exc:
                raise ValueError(
                    f"Строка {row_index}, основная марка: {exc}"
                ) from exc
            if not primary and any(str(cell).strip() for cell in row):
                raise ValueError(f"Строка {row_index}: нет основной марки")
            if not primary:
                continue
            for column_index in relation_indexes:
                if not str(row[column_index]).strip():
                    continue
                try:
                    related = self.normalizer.parse_strict(row[column_index])
                except ValueError as exc:
                    raise ValueError(
                        f"Строка {row_index}, столбец {column_index + 1}: {exc}"
                    ) from exc
                for first in primary:
                    for second in related:
                        for relation_type in RELATION_TYPES[column_types[column_index]]:
                            result.append((first.brand, relation_type, second.brand))
        if not result:
            raise ValueError("В блоке нет заполненных связей")
        return list(dict.fromkeys(result))

    def append(self, rows: list[list[str]], column_types: list[str]) -> dict[str, Any]:
        additions = self.build(rows, column_types)
        with self._lock:
            existing = self._existing()
            known = set(existing)
            new_rows = [row for row in additions if row not in known]
            combined = existing + new_rows
            output = io.StringIO(newline="")
            writer = csv.writer(output, delimiter=";", lineterminator="\n")
            writer.writerow(RELATION_HEADERS)
            writer.writerows(combined)
            temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text("\ufeff" + output.getvalue(), encoding="utf-8")
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
        return {
            "added": len(new_rows),
            "total": len(combined),
            "path": str(self.path),
            "rows": [list(row) for row in additions],
        }


def normalize_text(value: str, catalog_path: Path) -> str:
    """Convenience string-in/string-out entry point for scripts and imports."""
    return GsmNormalizer(catalog_path).normalize_text(value)
