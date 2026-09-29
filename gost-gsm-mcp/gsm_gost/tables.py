from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


SUPPORTED_SUFFIXES = {".html", ".htm", ".md"}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\u00a0", " ").replace("\u200b", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def inline_text(value: Any) -> str:
    return re.sub(r"\s+", " ", clean_text(value)).strip()


def comparison_text(value: Any) -> str:
    text = inline_text(value).casefold().replace("ё", "е")
    text = text.translate(
        str.maketrans({"«": "", "»": "", '"': "", "–": "-", "—": "-"})
    )
    return text


def collect_source_files(source: Path) -> list[Path]:
    if source.is_file():
        if source.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ValueError("Source must be an .html, .htm, or .md file")
        return [source.resolve()]

    files = sorted(
        path.resolve()
        for path in source.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )
    groups: dict[tuple[str, str], list[Path]] = {}
    for path in files:
        groups.setdefault(
            (str(path.parent).casefold(), path.stem.casefold()), []
        ).append(path)

    selected: list[Path] = []
    for key in sorted(groups):
        group = groups[key]
        html = [path for path in group if path.suffix.lower() in {".html", ".htm"}]
        selected.extend(
            html[:1] or [path for path in group if path.suffix.lower() == ".md"]
        )
    return selected


def _html_matrix(table: Any) -> list[list[str]]:
    matrix: list[list[str]] = []
    occupied: dict[tuple[int, int], str] = {}
    rows = table.find_all("tr")
    for row_index, tr in enumerate(rows):
        row: list[str] = []
        column_index = 0

        def append_occupied() -> None:
            nonlocal column_index
            while (row_index, column_index) in occupied:
                row.append(occupied.pop((row_index, column_index)))
                column_index += 1

        append_occupied()
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            cells = tr.find_all(["th", "td"])
        for cell in cells:
            append_occupied()
            value = clean_text(cell.get_text("\n", strip=True))
            try:
                rowspan = max(1, int(cell.get("rowspan", 1)))
                colspan = max(1, int(cell.get("colspan", 1)))
            except (TypeError, ValueError):
                rowspan = colspan = 1
            for offset in range(colspan):
                row.append(value)
                for row_offset in range(1, rowspan):
                    occupied[(row_index + row_offset, column_index + offset)] = value
            column_index += colspan
        append_occupied()
        matrix.append(row)

    width = max((len(row) for row in matrix), default=0)
    for row in matrix:
        row.extend([""] * (width - len(row)))
    return matrix


def _split_markdown_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith(r"\|"):
        line = line[:-1]
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append(
                clean_text(
                    "".join(current).replace("<br>", "\n").replace("<br/>", "\n")
                )
            )
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    cells.append(
        clean_text("".join(current).replace("<br>", "\n").replace("<br/>", "\n"))
    )
    return cells


def _markdown_matrices(text: str) -> list[list[list[str]]]:
    separator = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$")
    lines = text.splitlines()
    result: list[list[list[str]]] = []
    index = 0
    while index + 1 < len(lines):
        if "|" not in lines[index] or not separator.match(lines[index + 1]):
            index += 1
            continue
        block = [lines[index]]
        index += 2
        while index < len(lines) and lines[index].strip() and "|" in lines[index]:
            block.append(lines[index])
            index += 1
        matrix = [_split_markdown_row(line) for line in block]
        width = max((len(row) for row in matrix), default=0)
        for row in matrix:
            row.extend([""] * (width - len(row)))
        if len(matrix) >= 2 and width >= 2:
            result.append(matrix)
    return result


def extract_tables(files: list[Path], relative_to: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    serial = 1
    document_order = 1
    for source in files:
        text = source.read_text(encoding="utf-8-sig", errors="replace")
        suffix = source.suffix.lower()
        candidates: list[tuple[str, list[list[str]]]] = []
        if suffix in {".html", ".htm"} or "<table" in text.casefold():
            soup = BeautifulSoup(text, "html.parser")
            candidates.extend(
                ("html", _html_matrix(table)) for table in soup.find_all("table")
            )
        if suffix == ".md":
            candidates.extend(
                ("markdown", matrix) for matrix in _markdown_matrices(text)
            )

        try:
            relative_name = str(source.relative_to(relative_to))
        except ValueError:
            relative_name = str(source)
        for source_format, matrix in candidates:
            width = max((len(row) for row in matrix), default=0)
            if len(matrix) < 2 or width < 2:
                continue
            result.append(
                {
                    "table_id": f"T{serial:06d}",
                    "document_order": document_order,
                    "source_file": relative_name,
                    "source_format": source_format,
                    "row_count": len(matrix),
                    "column_count": width,
                    "matrix": matrix,
                }
            )
            serial += 1
            document_order += 1
    return result


def collapse_headers(matrix: list[list[str]], depth: int) -> list[str]:
    width = max((len(row) for row in matrix), default=0)
    headers: list[str] = []
    for column in range(width):
        parts: list[str] = []
        for row in matrix[:depth]:
            value = clean_text(row[column]) if column < len(row) else ""
            if value and (
                not parts or comparison_text(value) != comparison_text(parts[-1])
            ):
                parts.append(value)
        headers.append(inline_text(" ".join(parts)))
    return headers


def _alias_score(header: str, aliases: list[str]) -> int:
    normalized = comparison_text(header)
    best = 0
    for alias in aliases:
        candidate = comparison_text(alias)
        exact_phrase = bool(
            candidate
            and re.search(
                rf"(?<![0-9a-zа-я]){re.escape(candidate)}(?![0-9a-zа-я])",
                normalized,
                flags=re.IGNORECASE,
            )
        )
        if exact_phrase:
            best = max(best, 10 + len(candidate.split()))
        else:
            words = [word for word in candidate.split() if len(word) > 1]
            if words:
                best = max(best, sum(word in normalized for word in words))
    return best


def evaluate_table(
    matrix: list[list[str]],
    aliases: dict[str, list[str]],
    required: list[str],
    optional: list[str],
) -> dict[str, Any]:
    best: dict[str, Any] = {
        "valid": False,
        "score": -1,
        "header_depth": 1,
        "headers": collapse_headers(matrix, 1),
        "column_map": {},
        "column_scores": {},
    }
    roles = required + optional
    for depth in range(1, min(4, len(matrix) - 1) + 1):
        headers = collapse_headers(matrix, depth)
        candidates: dict[str, list[tuple[int, int]]] = {}
        for role in roles:
            scored = [
                (_alias_score(header, aliases.get(role, [role])), index)
                for index, header in enumerate(headers)
            ]
            candidates[role] = sorted(
                [value for value in scored if value[0] > 0],
                key=lambda value: (-value[0], value[1]),
            )

        used: set[int] = set()
        column_map: dict[str, int | None] = {}
        column_scores: dict[str, int] = {}
        order = sorted(
            roles,
            key=lambda role: (
                role not in required,
                -(candidates[role][0][0] if candidates[role] else 0),
            ),
        )
        for role in order:
            choice = next(
                (value for value in candidates[role] if value[1] not in used), None
            )
            column_map[role] = choice[1] if choice else None
            column_scores[role] = choice[0] if choice else 0
            if choice:
                used.add(choice[1])

        valid = all(column_map.get(role) is not None for role in required)
        score = sum(
            column_scores[role] * (5 if role in required else 1) for role in roles
        )
        if valid and score > best["score"]:
            best = {
                "valid": True,
                "score": score,
                "header_depth": depth,
                "headers": headers,
                "column_map": column_map,
                "column_scores": column_scores,
            }
    return best
