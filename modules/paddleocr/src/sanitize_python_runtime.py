#!/usr/bin/env python3
"""Remove build-machine file URLs from a prepared Python runtime.

Installing a wheel by local path may create PEP 610 ``direct_url.json``
metadata containing the online computer's drive and project directory.  The
metadata is not needed to run the package offline, so remove only local-file
records and keep each distribution's CSV ``RECORD`` internally consistent.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def remove_record_row(record: Path, target: str) -> None:
    if not record.is_file():
        raise RuntimeError(f"Distribution RECORD is missing: {record}")
    with record.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    normalized_target = target.replace("\\", "/").casefold()
    filtered = [
        row
        for row in rows
        if not row or row[0].replace("\\", "/").casefold() != normalized_target
    ]
    temporary = record.with_name(record.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, lineterminator="\n").writerows(filtered)
    os.replace(temporary, record)


def sanitize(runtime: Path) -> list[str]:
    site_packages = runtime / "Lib" / "site-packages"
    if not site_packages.is_dir():
        raise RuntimeError(f"site-packages is missing: {site_packages}")

    removed: list[str] = []
    for metadata in sorted(site_packages.glob("*.dist-info/direct_url.json")):
        try:
            payload = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Cannot parse {metadata}: {exc}") from exc
        url = payload.get("url")
        if not isinstance(url, str):
            raise RuntimeError(f"PEP 610 URL is missing in {metadata}")
        if not url.casefold().startswith("file:"):
            continue

        relative = metadata.relative_to(site_packages).as_posix()
        remove_record_row(metadata.parent / "RECORD", relative)
        metadata.unlink()
        removed.append(relative)
        print(f"[CLEAN] {relative}", flush=True)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--record", required=True, type=Path)
    args = parser.parse_args()

    runtime = args.runtime.resolve()
    removed = sanitize(runtime)
    atomic_json(
        args.record,
        {
            "schema": SCHEMA_VERSION,
            "runtime": runtime.name,
            "removed_local_direct_urls": removed,
        },
    )
    print(f"[OK] Removed {len(removed)} local direct URL record(s)", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
