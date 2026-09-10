#!/usr/bin/env python3
"""Fail closed when a prepared runtime still depends on its build location.

The audit deliberately records only paths relative to the audited runtime.  The
build root itself must not be copied into the JSON record that is later bundled
for the offline machine.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote


SCHEMA_VERSION = 2
READ_CHUNK_SIZE = 1024 * 1024

# Text that can affect launch, imports, package discovery or runtime config.
# Python source is included because generated wrappers and package helpers are
# frequently .py files rather than .cmd files on Windows.
TEXT_SUFFIXES = {
    ".bat",
    ".cfg",
    ".cgi",
    ".cmd",
    ".cmake",
    ".cnf",
    ".conf",
    ".config",
    ".env",
    ".fish",
    ".ini",
    ".js",
    ".json",
    ".mjs",
    ".cjs",
    ".pth",
    "._pth",
    ".egg-link",
    ".properties",
    ".ps1",
    ".psd1",
    ".psm1",
    ".py",
    ".pyw",
    ".reg",
    ".service",
    ".sh",
    ".tcl",
    ".toml",
    ".txt",
    ".url",
    ".vbs",
    ".wsf",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}

TEXT_NAMES = {
    ".npmrc",
    ".pypirc",
    ".yarnrc",
    "activate",
    "activate_this.py",
    "direct_url.json",
    "entry_points.txt",
    "install-script.py",
    "installer",
    "metadata",
    "orig-prefix.txt",
    "pip.conf",
    "pip.ini",
    "pyvenv.cfg",
    "record",
    "sitecustomize.py",
    "usercustomize.py",
    "wheel",
}

TEXT_TREE_NAMES = {"bin", "config", "etc", "scripts"}
BINARY_LAUNCH_SUFFIXES = {".exe", ".com", ".lnk"}

FILE_ATTRIBUTE_REPARSE_POINT = getattr(
    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400
)
IO_REPARSE_TAG_MOUNT_POINT = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
IO_REPARSE_TAG_SYMLINK = getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C)


@dataclass(frozen=True)
class EncodedNeedle:
    label: str
    encoding: str
    value: bytes
    folded: bytes


def _path_forms(build_root: str) -> dict[str, str]:
    """Return common serializations without exposing them in the report."""

    original = build_root.strip().strip('"')
    if len(original) > 3:
        original = original.rstrip("\\/")
    backslash = original.replace("/", "\\")
    forward_slash = original.replace("\\", "/")
    forms = {
        "native": original,
        "backslash": backslash,
        "forward-slash": forward_slash,
        "json-escaped-backslash": backslash.replace("\\", "\\\\"),
        "url-encoded-forward-slash": quote(forward_slash, safe="/"),
        "url-encoded": quote(original, safe=""),
    }
    return {name: value for name, value in forms.items() if value}


def _encoded_needles(build_root: str) -> list[EncodedNeedle]:
    encodings = ["utf-8", "utf-16-le", "utf-16-be"]
    if os.name == "nt":
        encodings.append("mbcs")

    needles: list[EncodedNeedle] = []
    seen: set[tuple[str, bytes]] = set()
    for label, value in _path_forms(build_root).items():
        for encoding in encodings:
            try:
                encoded = value.encode(encoding)
            except (LookupError, UnicodeEncodeError):
                continue
            folded = encoded.lower()
            key = (encoding, folded)
            if not encoded or key in seen:
                continue
            seen.add(key)
            needles.append(EncodedNeedle(label, encoding, encoded, folded))
    return needles


def _is_text_launch_or_config(relative: Path) -> bool:
    name = relative.name.casefold()
    suffix = relative.suffix.casefold()
    parts = [part.casefold() for part in relative.parts[:-1]]
    in_text_tree = any(
        part in TEXT_TREE_NAMES
        or part.endswith(".dist-info")
        or part.endswith(".egg-info")
        or part.endswith(".data")
        for part in parts
    )
    return name in TEXT_NAMES or suffix in TEXT_SUFFIXES or in_text_tree


def _is_scripts_executable(relative: Path) -> bool:
    return relative.suffix.casefold() == ".exe" and any(
        part.casefold() == "scripts" for part in relative.parts[:-1]
    )


def _io_path(path: Path) -> str:
    """Return a Win32 extended-length path for filesystem reads.

    The prepared dependency trees contain a few legitimate files whose full
    names exceed MAX_PATH.  Directory enumeration can still expose those files
    on Windows even when a subsequent normal ``open`` fails with ENOENT.
    """

    value = os.path.abspath(os.fspath(path))
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _is_pe(path: Path) -> bool:
    """Recognize a PE image while still scanning non-PE files named *.exe."""

    try:
        with open(_io_path(path), "rb") as stream:
            header = stream.read(64)
            if len(header) < 64 or header[:2] != b"MZ":
                return False
            pe_offset = int.from_bytes(header[60:64], "little")
            if pe_offset < 64 or pe_offset > 16 * 1024 * 1024:
                return False
            stream.seek(pe_offset)
            return stream.read(4) == b"PE\0\0"
    except OSError:
        return False


def _scan_for_needles(
    path: Path, needles: list[EncodedNeedle]
) -> tuple[list[str], int]:
    """Search a file in bounded memory, including matches across chunk edges."""

    if not needles:
        return [], 0
    overlap = max(len(needle.value) for needle in needles) - 1
    tail = b""
    matched: set[str] = set()
    bytes_read = 0
    with open(_io_path(path), "rb") as stream:
        while True:
            chunk = stream.read(READ_CHUNK_SIZE)
            if not chunk:
                break
            bytes_read += len(chunk)
            block = tail + chunk
            folded_block = block.lower()
            for needle in needles:
                if needle.value in block or needle.folded in folded_block:
                    matched.add(f"{needle.label}:{needle.encoding}")
            tail = block[-overlap:] if overlap else b""
    return sorted(matched), bytes_read


def _reparse_tag(file_stat: os.stat_result) -> int | None:
    value = getattr(file_stat, "st_reparse_tag", None)
    return int(value) if value is not None else None


def _filesystem_risk(path: Path, file_stat: os.stat_result) -> tuple[str, int | None] | None:
    tag = _reparse_tag(file_stat)
    attributes = int(getattr(file_stat, "st_file_attributes", 0))

    if path.is_symlink() or stat.S_ISLNK(file_stat.st_mode) or tag == IO_REPARSE_TAG_SYMLINK:
        return "symlink", tag

    is_junction = False
    junction_probe = getattr(path, "is_junction", None)
    if callable(junction_probe):
        try:
            is_junction = bool(junction_probe())
        except OSError:
            is_junction = False
    if is_junction or tag == IO_REPARSE_TAG_MOUNT_POINT:
        return "junction", tag
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        return "reparse-point", tag
    if not stat.S_ISDIR(file_stat.st_mode) and not stat.S_ISREG(file_stat.st_mode):
        return "special-filesystem-object", tag
    return None


def _relative(path: Path, root: Path) -> str:
    if path == root:
        return "."
    return path.relative_to(root).as_posix()


def _safe_error_message(error: OSError, root: Path, build_root: str) -> str:
    message = str(error)
    replacements = {
        str(root): "<audit-root>",
        str(root).replace("\\", "/"): "<audit-root>",
    }
    replacements.update({value: "<build-root>" for value in _path_forms(build_root).values()})
    for value, replacement in sorted(
        replacements.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if value:
            message = re.sub(re.escape(value), replacement, message, flags=re.IGNORECASE)
    return message


def _scan_error(
    relative: str, operation: str, error: OSError, root: Path, build_root: str
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": relative,
        "operation": operation,
        "error": type(error).__name__,
        "message": _safe_error_message(error, root, build_root),
    }
    if error.errno is not None:
        result["errno"] = error.errno
    winerror = getattr(error, "winerror", None)
    if winerror is not None:
        result["winerror"] = winerror
    return result


def _walk_regular_files(
    root: Path,
    filesystem_findings: list[dict[str, Any]],
    scan_errors: list[dict[str, Any]],
    build_root: str,
    counters: dict[str, int],
) -> Iterator[tuple[Path, Path, os.stat_result]]:
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name.casefold())
        except OSError as error:
            scan_errors.append(
                _scan_error(_relative(directory, root), "scandir", error, root, build_root)
            )
            continue

        child_directories: list[Path] = []
        for entry in entries:
            path = Path(entry.path)
            relative_path = path.relative_to(root)
            relative = relative_path.as_posix()
            try:
                file_stat = entry.stat(follow_symlinks=False)
            except OSError as error:
                scan_errors.append(
                    _scan_error(relative, "lstat", error, root, build_root)
                )
                continue

            risk = _filesystem_risk(path, file_stat)
            if risk is not None:
                kind, tag = risk
                finding: dict[str, Any] = {"path": relative, "kind": kind}
                if tag is not None:
                    finding["reparse_tag"] = f"0x{tag:08x}"
                filesystem_findings.append(finding)
                continue

            if stat.S_ISDIR(file_stat.st_mode):
                counters["directories_seen"] += 1
                child_directories.append(path)
            elif stat.S_ISREG(file_stat.st_mode):
                counters["files_seen"] += 1
                yield path, relative_path, file_stat

        # Reverse push preserves the sorted order with a LIFO stack.
        stack.extend(reversed(child_directories))


def audit(root: Path, build_root: str) -> dict[str, Any]:
    filesystem_findings: list[dict[str, Any]] = []
    path_leaks: list[dict[str, Any]] = []
    scan_errors: list[dict[str, Any]] = []
    counters = {
        "directories_seen": 0,
        "files_seen": 0,
        "files_ignored": 0,
        "text_launch_config_files_scanned": 0,
        "binary_launch_files_scanned": 0,
        "exe_files_scanned": 0,
        "pe_files_scanned": 0,
        "scripts_exe_launchers_scanned": 0,
        "bytes_scanned": 0,
    }

    needles = _encoded_needles(build_root)
    if not build_root.strip():
        scan_errors.append(
            {
                "path": ".",
                "operation": "configuration",
                "error": "EmptyBuildRoot",
                "message": "--build-root must not be empty",
            }
        )

    try:
        root_stat = os.lstat(root)
    except OSError as error:
        scan_errors.append(_scan_error(".", "lstat", error, root, build_root))
        root_stat = None

    if root_stat is not None:
        risk = _filesystem_risk(root, root_stat)
        if risk is not None:
            kind, tag = risk
            finding: dict[str, Any] = {"path": ".", "kind": kind}
            if tag is not None:
                finding["reparse_tag"] = f"0x{tag:08x}"
            filesystem_findings.append(finding)
        elif not stat.S_ISDIR(root_stat.st_mode):
            scan_errors.append(
                {
                    "path": ".",
                    "operation": "validate-root",
                    "error": "NotADirectory",
                    "message": "The audit root is not a directory",
                }
            )
        else:
            counters["directories_seen"] = 1
            for path, relative_path, _ in _walk_regular_files(
                root, filesystem_findings, scan_errors, build_root, counters
            ):
                suffix = relative_path.suffix.casefold()
                is_binary_launcher = suffix in BINARY_LAUNCH_SUFFIXES
                is_text_candidate = _is_text_launch_or_config(relative_path)
                if not is_binary_launcher and not is_text_candidate:
                    counters["files_ignored"] += 1
                    continue

                category = "text-launch-or-config"
                pe_image: bool | None = None
                if is_binary_launcher:
                    category = "binary-launcher"
                    counters["binary_launch_files_scanned"] += 1
                    if suffix == ".exe":
                        counters["exe_files_scanned"] += 1
                        pe_image = _is_pe(path)
                        if pe_image:
                            counters["pe_files_scanned"] += 1
                        if _is_scripts_executable(relative_path):
                            category = "scripts-exe-launcher"
                            counters["scripts_exe_launchers_scanned"] += 1
                else:
                    counters["text_launch_config_files_scanned"] += 1

                try:
                    matches, bytes_read = _scan_for_needles(path, needles)
                    counters["bytes_scanned"] += bytes_read
                except OSError as error:
                    scan_errors.append(
                        _scan_error(
                            relative_path.as_posix(), "read", error, root, build_root
                        )
                    )
                    continue
                if matches:
                    finding = {
                        "path": relative_path.as_posix(),
                        "category": category,
                        "matches": matches,
                    }
                    if pe_image is not None:
                        finding["pe_image"] = pe_image
                    path_leaks.append(finding)

    filesystem_findings.sort(key=lambda item: (item["path"].casefold(), item["kind"]))
    path_leaks.sort(key=lambda item: item["path"].casefold())
    scan_errors.sort(key=lambda item: (item["path"].casefold(), item["operation"]))
    passed = not path_leaks and not filesystem_findings and not scan_errors

    summary = {
        **counters,
        "path_leaks": len(path_leaks),
        "filesystem_objects_rejected": len(filesystem_findings),
        "scan_errors": len(scan_errors),
        "passed": passed,
    }
    symlinks = [
        finding["path"] for finding in filesystem_findings if finding["kind"] == "symlink"
    ]
    reparse_points = [
        finding["path"]
        for finding in filesystem_findings
        if finding["kind"] in {"junction", "reparse-point"}
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        # Keep the legacy short root field without leaking an absolute path.
        "root": root.name,
        "policy": {
            "build_root_is_redacted": True,
            "path_match_encodings": ["utf-8", "utf-16-le", "utf-16-be"]
            + (["mbcs"] if os.name == "nt" else []),
            "text_suffixes": sorted(TEXT_SUFFIXES),
            "text_names": sorted(TEXT_NAMES),
            "binary_launch_suffixes": sorted(BINARY_LAUNCH_SUFFIXES),
            "filesystem_objects_rejected": [
                "symlink",
                "junction",
                "reparse-point",
                "special-filesystem-object",
            ],
        },
        "summary": summary,
        "findings": {
            "absolute_path_leaks": path_leaks,
            "filesystem_links_and_reparse_points": filesystem_findings,
            "scan_errors": scan_errors,
        },
        # Backward-compatible summary fields used by earlier reports.
        "critical_files_checked": counters["text_launch_config_files_scanned"]
        + counters["binary_launch_files_scanned"],
        "absolute_path_leaks": [finding["path"] for finding in path_leaks],
        "symlinks": symlinks,
        "reparse_points": reparse_points,
        "scan_errors": scan_errors,
        "passed": passed,
    }


def _write_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reject build-path leaks and filesystem links in a portable runtime."
    )
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--build-root", required=True)
    parser.add_argument("--record", required=True, type=Path)
    args = parser.parse_args()

    # abspath is intentionally used instead of resolve(): resolve() would follow
    # a symlink or junction supplied as the audit root before it can be rejected.
    root = Path(os.path.abspath(os.fspath(args.root)))
    record = audit(root, args.build_root)
    try:
        _write_record(args.record, record)
    except OSError as error:
        print(f"[ERROR] Could not write portability record: {error}", file=sys.stderr)
        return 2
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
