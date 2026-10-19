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
APPLICABILITY_TYPES = {
    "applicable_ground": "Применимо для наземной техники",
    "applicable_air": "Применимо для авиа техники",
    "applicable_marine": "Применимо для морской техники",
}
APPLICABILITY_HEADERS = tuple(APPLICABILITY_TYPES.values())
RELATION_KEY_HEADERS = ("Первая марка", "Тип связи", "Вторая марка")
NOTES_HEADER = "Примечание"
RELATION_HEADERS = (*RELATION_KEY_HEADERS, NOTES_HEADER)
COLUMN_TYPES = {
    "other": "другой",
    "primary": "основная марка ГСМ",
    "duplicate": "дублирующая марка ГСМ",
    "reserve": "резервная марка ГСМ",
    "foreign": "зарубежная марка ГСМ",
    "duplicate_reserve": "дублирующая и резервная одновременно",
    "applicable_ground": "применимо для наземной техники",
    "applicable_air": "применимо для авиа техники",
    "applicable_marine": "применимо для морской техники",
}
RELATION_TYPES = {
    "duplicate": ("дублирующая",),
    "reserve": ("резервная",),
    "foreign": ("зарубежная",),
    "duplicate_reserve": ("дублирующая", "резервная"),
}

_PO_RE = re.compile(r"\s+по\s+", re.IGNORECASE)
_UNRESOLVED_RE = re.compile(r"^\s*-\s+по\s+(.+?)\s*$", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^0-9A-ZА-Я]+")
_DOCUMENT_START_RE = re.compile(
    r"(?:^|\s)(?:ГОСТ|ОСТ|ТУ|СТО|ASTM|ISO|DIN|SAE|API)(?:\s|$)"
)
_RAW_DOCUMENT_START_RE = re.compile(
    r"(?:^|\s)(?:ГОСТ|ОСТ|ТУ|СТО|ASTM|ISO|DIN|SAE|API)(?:\s|$)",
    re.IGNORECASE,
)
_HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
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


def _separator_spans(value: str) -> list[tuple[int, int]]:
    """Return comma/newline/``и`` separators outside parentheses."""
    spans: list[tuple[int, int]] = []
    depth = 0
    index = 0
    while index < len(value):
        char = value[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and char == ",":
            spans.append((index, index + 1))
        elif depth == 0 and char in "\r\n":
            end = index + 1
            while end < len(value) and value[end] in "\r\n":
                end += 1
            spans.append((index, end))
            index = end - 1
        elif depth == 0 and char.casefold() == "и":
            previous = value[index - 1] if index else " "
            following = value[index + 1] if index + 1 < len(value) else " "
            if not previous.isalnum() and not following.isalnum():
                spans.append((index, index + 1))
        index += 1
    return spans


def _split_outside_parentheses(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    for left, right in _separator_spans(value):
        part = value[start:left].strip()
        if part:
            parts.append(part)
        start = right
    tail = value[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _split_semicolon_groups(value: str) -> list[str]:
    """Apply the hard ``;`` boundary without parsing parenthetical text."""
    parts: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            part = value[start:index].strip()
            if part:
                parts.append(part)
            start = index + 1
    tail = value[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _split_paired_clauses(value: str) -> list[str]:
    """Split ``A по X и B по Y`` without touching parenthetical и."""
    spans = _separator_spans(value)
    selected: list[tuple[int, int]] = []
    clause_start = 0
    for position, span in enumerate(spans):
        next_left = spans[position + 1][0] if position + 1 < len(spans) else len(value)
        if _PO_RE.search(value[clause_start : span[0]]) and _PO_RE.search(
            value[span[1] : next_left]
        ):
            selected.append(span)
            clause_start = span[1]
    if not selected:
        return [value]
    parts: list[str] = []
    start = 0
    for left, right in selected:
        part = value[start:left].strip()
        if part:
            parts.append(part)
        start = right
    tail = value[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _apply_shared_parenthetical_suffix(brands: list[str]) -> list[str]:
    """Copy a trailing qualifier from the last listed brand to earlier brands."""
    if len(brands) < 2:
        return brands
    last = brands[-1]
    suffix_at = last.find("(")
    if suffix_at <= 0:
        return brands
    suffix = last[suffix_at:].strip()
    if not suffix:
        return brands
    return [
        brand if "(" in brand else f"{brand} {suffix}".strip()
        for brand in brands
    ]


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
        self._lock = threading.RLock()
        self._catalog_state: tuple[int, int] | None = None
        self.entries: tuple[CatalogEntry, ...] = ()
        self.documents: tuple[str, ...] = ()
        self._pairs: dict[tuple[str, str], CatalogEntry] = {}
        self._applicability: dict[tuple[str, str], dict[str, str]] = {}
        self._reload_if_changed()

    def _file_state(self) -> tuple[int, int]:
        try:
            stat = self.catalog_path.stat()
        except FileNotFoundError as exc:
            raise ValueError(
                f"Справочник марок не найден: {self.catalog_path}"
            ) from exc
        if not self.catalog_path.is_file():
            raise ValueError(f"Справочник марок не найден: {self.catalog_path}")
        return stat.st_mtime_ns, stat.st_size

    def _reload_if_changed(self) -> None:
        """Reload an externally edited catalog before the next operation."""
        state = self._file_state()
        with self._lock:
            if state == self._catalog_state:
                return
            entries, applicability = self._read_entries()
            self.entries = tuple(entries)
            self.documents = tuple(
                dict.fromkeys(entry.document for entry in entries)
            )
            self._pairs = {
                (_match_text(entry.brand), _match_text(entry.document)): entry
                for entry in entries
            }
            self._applicability = applicability
            # Read the state again so a concurrent file replacement is noticed by
            # the following request rather than being treated as already loaded.
            self._catalog_state = self._file_state()

    def _read_entries(
        self,
    ) -> tuple[list[CatalogEntry], dict[tuple[str, str], dict[str, str]]]:
        rows = _dict_rows(self.catalog_path)
        entries: list[CatalogEntry] = []
        applicability: dict[tuple[str, str], dict[str, str]] = {}
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
            flags: dict[str, str] = {}
            for column_type, header in APPLICABILITY_TYPES.items():
                flag_key = keys.get(header.casefold())
                flag = str(row.get(flag_key) or "").strip() if flag_key else ""
                if flag not in {"", "+", "-"}:
                    raise ValueError(
                        f"{self.catalog_path}: в столбце «{header}» строки "
                        f"{row_number} допустимы только +, - или пустая ячейка"
                    )
                flags[column_type] = flag
            if identity not in seen:
                seen.add(identity)
                entries.append(CatalogEntry(brand, document))
                applicability[identity] = flags
        if not entries:
            raise ValueError(f"Справочник марок пуст: {self.catalog_path}")
        return entries, applicability

    def _exact_entry(self, value: str) -> CatalogEntry | None:
        pieces = _PO_RE.split(str(value or "").strip(), maxsplit=1)
        if len(pieces) != 2:
            return None
        return self._pairs.get(
            (_match_text(pieces[0].strip()), _match_text(pieces[1].strip()))
        )

    def _exact_item(self, value: str) -> dict[str, Any] | None:
        """Describe a catalog pair without rewriting the user's cell text."""
        source = str(value or "").strip()
        entry = self._exact_entry(source)
        if entry is None:
            return None
        brand, document = (
            part.strip() for part in _PO_RE.split(source, maxsplit=1)
        )
        identity = (_match_text(entry.brand), _match_text(entry.document))
        candidates = []
        for candidate in self._candidates([entry.document], entry.brand):
            public = candidate.public()
            candidate_identity = (
                _match_text(candidate.brand),
                _match_text(candidate.document),
            )
            if candidate_identity == identity:
                public = {"brand": brand, "document": document, "value": source}
            candidates.append(public)
        return {
            "source": source,
            "brand": brand,
            "document": document,
            "value": source,
            "resolved": True,
            "candidates": candidates,
        }

    def _documents_in(self, text: str) -> list[tuple[str, float]]:
        normalized_text = _match_text(text)
        exact: list[tuple[int, str]] = []
        for document in self.documents:
            match = re.search(
                rf"(?<![0-9A-ZА-Я./-]){re.escape(_match_text(document))}"
                r"(?![0-9A-ZА-Я./-])",
                normalized_text,
            )
            if match:
                exact.append((match.start(), document))
        if exact:
            return [
                (document, 1.0) for _, document in sorted(exact)
            ]
        scored = sorted(
            (
                (
                    document,
                    _similarity(text, document),
                )
                for document in self.documents
            ),
            key=lambda item: (-item[1], item[0]),
        )
        if not scored or scored[0][1] < 0.78:
            return []
        best = scored[0][1]
        return [item for item in scored if item[1] >= max(0.78, best - 0.035)]

    def _resolution_options(self, documents: Iterable[str]) -> list[CatalogEntry]:
        allowed = {_match_text(document) for document in documents}
        options = [
            entry
            for entry in self.entries
            if not allowed or _match_text(entry.document) in allowed
        ]
        return options or list(self.entries)

    def _candidates(
        self, documents: Iterable[str], brand_part: str
    ) -> list[CatalogEntry]:
        matching_documents = {_match_text(document) for document in documents}
        matching_brand = _match_text(brand_part)
        return sorted(
            self.entries,
            key=lambda entry: (
                0
                if _match_text(entry.document) in matching_documents
                or bool(matching_brand)
                and _match_text(entry.brand) == matching_brand
                else 1,
                _match_text(entry.brand),
                _match_text(entry.document),
            ),
        )

    def _missing_proposal(
        self, fragment: str, documents: Iterable[str]
    ) -> CatalogEntry | None:
        """Extract an explicit brand/document pair which is absent from CSV."""
        raw = str(fragment or "").strip()
        known_documents = list(documents)
        brand = ""
        document = ""
        paired = _PO_RE.split(raw, maxsplit=1)
        if len(paired) == 2:
            brand, document = paired
        elif len(known_documents) == 1:
            document = known_documents[0]
            match = re.search(re.escape(document), raw, re.IGNORECASE)
            brand = raw[: match.start()] if match else raw
        else:
            match = _RAW_DOCUMENT_START_RE.search(raw)
            if match:
                brand, document = raw[: match.start()], raw[match.start() :]
        brand = re.sub(r"\s+по\s*$", "", brand, flags=re.IGNORECASE).strip(" ,;-")
        document = document.strip(" ,;-")
        if not brand or not document or brand == "-" or document == "-":
            return None
        proposal = CatalogEntry(brand, document)
        identity = (_match_text(proposal.brand), _match_text(proposal.document))
        return None if identity in self._pairs else proposal

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
        unresolved = _UNRESOLVED_RE.match(fragment)
        if unresolved:
            raw_document = unresolved.group(1).strip() or "-"
            document_scores = self._documents_in(raw_document)
            documents = [item[0] for item in document_scores]
            document = documents[0] if len(documents) == 1 else raw_document
            return {
                "source": fragment.strip(),
                "brand": "-",
                "document": document,
                "value": f"- по {document}",
                "resolved": False,
                "candidates": [
                    entry.public() for entry in self._candidates(documents, "")
                ],
            }

        local_document_scores = self._documents_in(fragment)
        local_documents = [item[0] for item in local_document_scores]
        documents = local_documents or global_documents
        options = self._resolution_options(documents)
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
            value = selected.value
            document = selected.document
        else:
            selected = None
            document = documents[0] if len(documents) == 1 else "-"
            value = f"- по {document}"
        candidates = self._candidates(documents, brand_part)
        result = {
            "source": fragment.strip(),
            "brand": selected.brand if selected else "-",
            "document": document,
            "value": value,
            "resolved": bool(selected),
            "candidates": [entry.public() for entry in candidates],
        }
        if not selected and (proposal := self._missing_proposal(fragment, documents)):
            result["catalogMissing"] = True
            result["proposal"] = proposal.public()
        return result

    def _expanded_fragments(
        self, raw: str, global_documents: list[str]
    ) -> list[tuple[str, bool]]:
        fragments: list[tuple[str, bool]] = []
        # A semicolon outside parentheses starts a new logical group.  Only
        # after that hard boundary do commas/newlines/conjunctions expand it.
        groups = _split_semicolon_groups(raw)
        for group in groups:
            # A complete catalog pair is already normalized.  In particular,
            # conjunctions inside its brand/document must not split it again.
            exact = self._exact_entry(group)
            if exact is not None:
                fragments.append((group.strip(), True))
                continue
            clauses = (
                _split_paired_clauses(group)
                if len(_PO_RE.findall(group)) > 1
                else [group]
            )
            for clause in clauses:
                if _UNRESOLVED_RE.match(clause):
                    fragments.append((clause.strip(), False))
                    continue
                local_documents = [
                    item[0] for item in self._documents_in(clause)
                ]
                documents = local_documents or global_documents
                brand_part = self._brand_part(clause, documents)
                brands = _apply_shared_parenthetical_suffix(
                    [
                        part.strip(" -")
                        for part in _split_outside_parentheses(brand_part)
                        if part.strip(" -")
                    ]
                )
                if not brands:
                    fragments.append((clause.strip(), False))
                elif documents:
                    fragments.extend(
                        (f"{brand} по {document}", False)
                        for brand in brands
                        for document in documents
                    )
                else:
                    explicit = _PO_RE.split(clause, maxsplit=1)
                    if len(explicit) != 2:
                        marker = _RAW_DOCUMENT_START_RE.search(clause)
                        explicit = (
                            [clause[: marker.start()], clause[marker.start() :]]
                            if marker
                            else []
                        )
                    if len(explicit) == 2 and all(part.strip() for part in explicit):
                        raw_brands = _apply_shared_parenthetical_suffix(
                            [
                                part.strip(" -")
                                for part in _split_outside_parentheses(explicit[0])
                                if part.strip(" -")
                            ]
                        )
                        fragments.extend(
                            (f"{brand} по {explicit[1].strip()}", False)
                            for brand in raw_brands
                        )
                    else:
                        fragments.extend((brand, False) for brand in brands)
        return fragments

    def analyze(self, value: str) -> dict[str, Any]:
        """Return normalized text plus per-name choices for unresolved fragments."""
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("Нечего нормализовать")
        self._reload_if_changed()
        with self._lock:
            global_document_scores = self._documents_in(raw)
            global_documents = [item[0] for item in global_document_scores]
            if len(global_documents) != 1:
                global_documents = []
            fragments = self._expanded_fragments(raw, global_documents)
            items = [
                item
                for fragment, preserve in fragments
                if (
                    item := (
                        self._exact_item(fragment)
                        if preserve
                        else self._analyze_fragment(fragment, global_documents)
                    )
                )
                is not None
            ]
            if not items:
                # A document without a recognized brand is an unresolved item.
                document = (
                    global_documents[0] if len(global_documents) == 1 else "-"
                )
                items = [
                    {
                        "source": raw,
                        "brand": "-",
                        "document": document,
                        "value": f"- по {document}",
                        "resolved": False,
                        "candidates": [
                            entry.public()
                            for entry in self._candidates(global_documents, "")
                        ],
                    }
                ]
            exact_groups = _split_semicolon_groups(raw)
            text = (
                raw
                if exact_groups
                and len(items) == len(exact_groups)
                and all(
                    self._exact_entry(group) is not None for group in exact_groups
                )
                else "; ".join(item["value"] for item in items)
            )
            return {
                "text": text,
                "items": items,
                "resolved": all(item["resolved"] for item in items),
            }

    def normalize_text(self, value: str) -> str:
        """Simple string-in/string-out API used outside the web application."""
        return str(self.analyze(value)["text"])

    def parse_strict(self, value: str) -> list[CatalogEntry]:
        """Validate a normalized cell and return its canonical catalog entries."""
        text = _HTML_COMMENT_RE.sub("", str(value or "")).strip()
        if not text:
            return []
        self._reload_if_changed()
        with self._lock:
            result: list[CatalogEntry] = []
            for part in _split_semicolon_groups(text):
                pieces = re.split(
                    r"\s+по\s+", part, maxsplit=1, flags=re.IGNORECASE
                )
                if len(pieces) != 2 or pieces[0].strip() == "-":
                    raise ValueError(f"не выбрана марка в «{part}»")
                entry = self._pairs.get(
                    (_match_text(pieces[0].strip()), _match_text(pieces[1].strip()))
                )
                if entry is None:
                    raise ValueError(f"нет в справочнике: «{part}»")
                result.append(entry)
            return result

    def append_entry(self, brand: str, document: str) -> dict[str, Any]:
        """Atomically append one user-confirmed pair to the source catalog."""
        entry = CatalogEntry(str(brand or "").strip(), str(document or "").strip())
        if not entry.brand or not entry.document:
            raise ValueError("Для добавления нужны марка и нормативный документ")
        if "\n" in entry.brand or "\r" in entry.brand:
            raise ValueError("Марка должна быть записана в одну строку")
        if "\n" in entry.document or "\r" in entry.document:
            raise ValueError("Нормативный документ должен быть записан в одну строку")
        identity = (_match_text(entry.brand), _match_text(entry.document))
        with self._lock:
            self._reload_if_changed()
            if identity in self._pairs:
                existing = self._pairs[identity]
                return {
                    "added": False,
                    "entry": existing.public(),
                    "total": len(self.entries),
                    "path": str(self.catalog_path),
                }
            text = _read_text(self.catalog_path)
            try:
                delimiter = csv.Sniffer().sniff(
                    text[:8192], delimiters=";,\t,"
                ).delimiter
            except csv.Error:
                delimiter = ";"
            rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
            if not rows:
                raise ValueError(f"Справочник марок пуст: {self.catalog_path}")
            headers = {
                value.strip().casefold(): index
                for index, value in enumerate(rows[0])
            }
            brand_index = headers.get(BRAND_HEADER.casefold())
            document_index = headers.get(DOCUMENT_HEADER.casefold())
            if brand_index is None or document_index is None:
                raise ValueError(
                    f"{self.catalog_path}: нужны столбцы «{BRAND_HEADER}» "
                    f"и «{DOCUMENT_HEADER}»"
                )
            new_row = [""] * len(rows[0])
            new_row[brand_index] = entry.brand
            new_row[document_index] = entry.document
            rows.append(new_row)
            output = io.StringIO(newline="")
            writer = csv.writer(output, delimiter=delimiter, lineterminator="\n")
            writer.writerows(rows)
            temporary = self.catalog_path.with_name(
                f".{self.catalog_path.name}.{uuid.uuid4().hex}.tmp"
            )
            try:
                temporary.write_text("\ufeff" + output.getvalue(), encoding="utf-8")
                os.replace(temporary, self.catalog_path)
            finally:
                temporary.unlink(missing_ok=True)
            self._catalog_state = None
            self._reload_if_changed()
        return {
            "added": True,
            "entry": entry.public(),
            "total": len(self.entries),
            "path": str(self.catalog_path),
        }

    def applicability_value(self, entry: CatalogEntry, column_type: str) -> str:
        """Return a saved applicability flag, including empty legacy values."""
        if column_type not in APPLICABILITY_TYPES:
            raise ValueError("Неизвестный тип применимости")
        self._reload_if_changed()
        identity = (_match_text(entry.brand), _match_text(entry.document))
        with self._lock:
            return self._applicability.get(identity, {}).get(column_type, "")

    def update_applicability(
        self,
        updates: Iterable[tuple[CatalogEntry, dict[str, str]]],
        ensure_columns: bool = False,
    ) -> dict[str, Any]:
        """Atomically add/update the three optional applicability columns."""
        merged: dict[tuple[str, str], dict[str, str]] = {}
        for entry, raw_flags in updates:
            identity = (_match_text(entry.brand), _match_text(entry.document))
            flags = merged.setdefault(identity, {})
            for column_type, raw_value in raw_flags.items():
                if column_type not in APPLICABILITY_TYPES:
                    raise ValueError("Неизвестный тип применимости")
                value = str(raw_value or "").strip()
                if value not in {"+", "-"}:
                    raise ValueError(
                        f"Для «{COLUMN_TYPES[column_type]}» допустимы только + или -"
                    )
                if column_type in flags and flags[column_type] != value:
                    raise ValueError(
                        f"Для марки «{entry.value}» указаны разные значения "
                        f"«{COLUMN_TYPES[column_type]}»"
                    )
                flags[column_type] = value
        if not merged and not ensure_columns:
            return {"updated": 0, "path": str(self.catalog_path)}

        with self._lock:
            self._reload_if_changed()
            text = _read_text(self.catalog_path)
            try:
                delimiter = csv.Sniffer().sniff(
                    text[:8192], delimiters=";,\t,"
                ).delimiter
            except csv.Error:
                delimiter = ";"
            rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
            if not rows:
                raise ValueError(f"Справочник марок пуст: {self.catalog_path}")
            headers = {
                value.strip().casefold(): index for index, value in enumerate(rows[0])
            }
            brand_index = headers.get(BRAND_HEADER.casefold())
            document_index = headers.get(DOCUMENT_HEADER.casefold())
            if brand_index is None or document_index is None:
                raise ValueError(
                    f"{self.catalog_path}: нужны столбцы «{BRAND_HEADER}» "
                    f"и «{DOCUMENT_HEADER}»"
                )
            for header in APPLICABILITY_HEADERS:
                if header.casefold() not in headers:
                    headers[header.casefold()] = len(rows[0])
                    rows[0].append(header)
            width = len(rows[0])
            updated = 0
            found: set[tuple[str, str]] = set()
            for row in rows[1:]:
                row.extend([""] * (width - len(row)))
                identity = (
                    _match_text(row[brand_index]),
                    _match_text(row[document_index]),
                )
                flags = merged.get(identity)
                if flags is None:
                    continue
                found.add(identity)
                for column_type, value in flags.items():
                    flag_index = headers[APPLICABILITY_TYPES[column_type].casefold()]
                    if row[flag_index].strip() != value:
                        row[flag_index] = value
                        updated += 1
            missing = set(merged) - found
            if missing:
                raise ValueError("Основная марка не найдена в справочнике")
            output = io.StringIO(newline="")
            writer = csv.writer(output, delimiter=delimiter, lineterminator="\n")
            writer.writerows(rows)
            temporary = self.catalog_path.with_name(
                f".{self.catalog_path.name}.{uuid.uuid4().hex}.tmp"
            )
            try:
                temporary.write_text("\ufeff" + output.getvalue(), encoding="utf-8")
                os.replace(temporary, self.catalog_path)
            finally:
                temporary.unlink(missing_ok=True)
            self._catalog_state = None
            self._reload_if_changed()
        return {"updated": updated, "path": str(self.catalog_path)}


class RelationshipStore:
    """Append normalized relationships to a local CSV atomically."""

    def __init__(self, path: Path, normalizer: GsmNormalizer) -> None:
        self.path = Path(path).expanduser().resolve()
        self.normalizer = normalizer
        self._lock = threading.RLock()
        if self.path.exists() and not self.path.is_file():
            raise ValueError(f"Путь CSV связей не является файлом: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _existing(self) -> list[tuple[str, str, str, str]]:
        if not self.path.is_file() or not self.path.stat().st_size:
            return []
        rows = _dict_rows(self.path)
        result: list[tuple[str, str, str, str]] = []
        for row in rows:
            keys = {str(key).strip().casefold(): key for key in row}
            if any(
                header.casefold() not in keys for header in RELATION_KEY_HEADERS
            ):
                raise ValueError(
                    f"{self.path}: нужны столбцы "
                    + ", ".join(RELATION_KEY_HEADERS)
                )
            values = [
                str(row.get(keys[header.casefold()]) or "").strip()
                for header in RELATION_KEY_HEADERS
            ]
            notes_key = keys.get(NOTES_HEADER.casefold())
            notes = str(row.get(notes_key) or "") if notes_key else ""
            if any(values) or notes:
                result.append((*values, notes))
        return result

    @staticmethod
    def _relation_key(
        first: str, relation_type: str, second: str
    ) -> tuple[str, str, str]:
        return (
            _match_text(first),
            _match_text(relation_type),
            _match_text(second),
        )

    def status_matrix(
        self, rows: list[list[str]], column_types: list[str]
    ) -> list[list[str]]:
        """Classify typed cells against the brand and relationship CSV files."""
        if not isinstance(rows, list) or not all(
            isinstance(row, list) for row in rows
        ):
            raise ValueError("Для статусов нужен массив строк таблицы")
        if not isinstance(column_types, list) or any(
            value and value not in COLUMN_TYPES for value in column_types
        ):
            raise ValueError("Типы столбцов повреждены")
        if any(len(row) != len(column_types) for row in rows):
            raise ValueError("Число ячеек не равно числу типов столбцов")

        parsed: list[list[list[CatalogEntry] | None]] = []
        statuses = [["" for _ in column_types] for _ in rows]
        for row_index, row in enumerate(rows):
            parsed_row: list[list[CatalogEntry] | None] = []
            for column_index, value in enumerate(row):
                column_type = column_types[column_index]
                cell = str(value).strip()
                if not column_type or column_type == "other" or not cell:
                    parsed_row.append([])
                    continue
                if column_type in APPLICABILITY_TYPES:
                    parsed_row.append([])
                    statuses[row_index][column_index] = (
                        "linked"
                        if cell in {"+", "-"}
                        else "applicability-invalid"
                    )
                    continue
                try:
                    entries = self.normalizer.parse_strict(cell)
                except ValueError:
                    parsed_row.append(None)
                    statuses[row_index][column_index] = "brand-missing"
                else:
                    parsed_row.append(entries)
                    statuses[row_index][column_index] = "linked"
            parsed.append(parsed_row)

        existing = {
            self._relation_key(first, relation_type, second)
            for first, relation_type, second, _ in self._existing()
        }
        primary_indexes = [
            index for index, value in enumerate(column_types) if value == "primary"
        ]
        relation_indexes = [
            index for index, value in enumerate(column_types) if value in RELATION_TYPES
        ]

        for row_index, row in enumerate(parsed):
            linked_related: list[bool] = []
            primary_entries = (
                row[primary_indexes[0]] if len(primary_indexes) == 1 else None
            )
            for column_index in relation_indexes:
                related_entries = row[column_index]
                if not related_entries:
                    continue
                linked = bool(primary_entries) and all(
                    self._relation_key(first.value, relation_type, second.value)
                    in existing
                    for first in primary_entries or []
                    for second in related_entries
                    for relation_type in RELATION_TYPES[column_types[column_index]]
                )
                statuses[row_index][column_index] = (
                    "linked" if linked else "relation-missing"
                )
                linked_related.append(linked)
            linked_applicability: list[bool] = []
            for column_index, column_type in enumerate(column_types):
                if column_type not in APPLICABILITY_TYPES:
                    continue
                flag = str(rows[row_index][column_index]).strip()
                if not flag:
                    continue
                if flag not in {"+", "-"}:
                    linked_applicability.append(False)
                    continue
                linked = bool(primary_entries) and all(
                    self.normalizer.applicability_value(first, column_type) == flag
                    for first in primary_entries or []
                )
                statuses[row_index][column_index] = (
                    "linked" if linked else "applicability-missing"
                )
                linked_applicability.append(linked)
            for primary_index in primary_indexes:
                if row[primary_index]:
                    linked_items = linked_related + linked_applicability
                    statuses[row_index][primary_index] = (
                        "linked"
                        if linked_items and all(linked_items)
                        else "relation-missing"
                    )
        return statuses

    def _build_export(
        self, rows: list[list[str]], column_types: list[str]
    ) -> tuple[
        list[tuple[str, str, str, str]],
        list[tuple[CatalogEntry, dict[str, str]]],
    ]:
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
        applicability_indexes = [
            i for i, value in enumerate(column_types) if value in APPLICABILITY_TYPES
        ]
        for column_type in APPLICABILITY_TYPES:
            if column_types.count(column_type) > 1:
                raise ValueError(
                    f"Тип «{COLUMN_TYPES[column_type]}» можно выбрать только один раз"
                )
        if not relation_indexes and not applicability_indexes:
            raise ValueError(
                "Нужен минимум один столбец связанных марок или применимости"
            )
        primary_index = primary_indexes[0]
        relations: list[tuple[str, str, str, str]] = []
        applicability: list[tuple[CatalogEntry, dict[str, str]]] = []
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
            flags: dict[str, str] = {}
            for column_index in applicability_indexes:
                value = str(row[column_index]).strip()
                if not value:
                    continue
                if value not in {"+", "-"}:
                    raise ValueError(
                        f"Строка {row_index}, столбец {column_index + 1}: "
                        "допустимы только + или -"
                    )
                flags[column_types[column_index]] = value
            if flags:
                applicability.extend((entry, flags.copy()) for entry in primary)
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
                            relations.append(
                                (first.value, relation_type, second.value, "")
                            )
        if not relations and not applicability:
            raise ValueError("В блоке нет заполненных связей или признаков применимости")
        return list(dict.fromkeys(relations)), applicability

    def build(
        self, rows: list[list[str]], column_types: list[str]
    ) -> list[tuple[str, str, str, str]]:
        relations, _ = self._build_export(rows, column_types)
        return relations

    def append(self, rows: list[list[str]], column_types: list[str]) -> dict[str, Any]:
        additions, applicability = self._build_export(rows, column_types)
        with self._lock:
            existing = self._existing()
            known = {row[:3] for row in existing}
            new_rows = [row for row in additions if row[:3] not in known]
            combined = existing + new_rows
            catalog_result = self.normalizer.update_applicability(
                applicability, ensure_columns=True
            )
            if additions:
                output = io.StringIO(newline="")
                writer = csv.writer(output, delimiter=";", lineterminator="\n")
                writer.writerow(RELATION_HEADERS)
                writer.writerows(combined)
                temporary = self.path.with_name(
                    f".{self.path.name}.{uuid.uuid4().hex}.tmp"
                )
                try:
                    temporary.write_text(
                        "\ufeff" + output.getvalue(), encoding="utf-8"
                    )
                    os.replace(temporary, self.path)
                finally:
                    temporary.unlink(missing_ok=True)
        return {
            "added": len(new_rows),
            "total": len(combined),
            "path": str(self.path),
            "rows": [list(row) for row in additions],
            "applicabilityUpdated": catalog_result["updated"],
            "catalogPath": catalog_result["path"],
        }


def normalize_text(value: str, catalog_path: Path) -> str:
    """Convenience string-in/string-out entry point for scripts and imports."""
    return GsmNormalizer(catalog_path).normalize_text(value)
