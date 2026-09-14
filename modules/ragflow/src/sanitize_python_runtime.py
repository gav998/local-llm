#!/usr/bin/env python3
"""Remove build-machine paths from a prepared Python runtime.

Installing a wheel by local path may create PEP 610 ``direct_url.json``
metadata containing the online computer's drive and project directory.  The
generated Windows entry-point launchers also embed the absolute interpreter
path.  Neither is needed by this service, which always invokes its bundled
interpreter directly, so remove both while keeping each distribution's CSV
``RECORD`` internally consistent.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def remove_record_row(record: Path, target: str) -> bool:
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
    if len(filtered) == len(rows):
        return False
    temporary = record.with_name(record.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, lineterminator="\n").writerows(filtered)
    os.replace(temporary, record)
    return True


def remove_console_launchers(runtime: Path, site_packages: Path) -> list[str]:
    scripts = runtime / "Scripts"
    if not scripts.is_dir():
        return []

    records = sorted(site_packages.glob("*.dist-info/RECORD"))
    removed: list[str] = []
    for launcher in sorted(scripts.rglob("*.exe")):
        if launcher.is_symlink() or not launcher.is_file():
            raise RuntimeError(f"Console launcher is not a regular file: {launcher}")
        record_target = os.path.relpath(launcher, site_packages).replace(os.sep, "/")
        owners = [
            record
            for record in records
            if remove_record_row(record, record_target)
        ]
        if not owners:
            raise RuntimeError(
                f"Console launcher has no owning distribution RECORD: {launcher}"
            )
        relative = launcher.relative_to(runtime).as_posix()
        launcher.unlink()
        removed.append(relative)
        print(f"[CLEAN] {relative}", flush=True)
    return removed


def sanitize(runtime: Path) -> tuple[list[str], list[str]]:
    site_packages = runtime / "Lib" / "site-packages"
    if not site_packages.is_dir():
        raise RuntimeError(f"site-packages is missing: {site_packages}")

    removed_direct_urls: list[str] = []
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
        removed_direct_urls.append(relative)
        print(f"[CLEAN] {relative}", flush=True)
    removed_launchers = remove_console_launchers(runtime, site_packages)
    return removed_direct_urls, removed_launchers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--record", required=True, type=Path)
    args = parser.parse_args()

    runtime = args.runtime.resolve()
    removed_direct_urls, removed_launchers = sanitize(runtime)
    atomic_json(
        args.record,
        {
            "schema": SCHEMA_VERSION,
            "runtime": runtime.name,
            "removed_local_direct_urls": removed_direct_urls,
            "removed_console_launchers": removed_launchers,
        },
    )
    print(
        f"[OK] Removed {len(removed_direct_urls)} local direct URL record(s) and "
        f"{len(removed_launchers)} absolute-path console launcher(s)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
