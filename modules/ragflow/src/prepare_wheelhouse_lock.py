#!/usr/bin/env python3
"""Create an exact hash lock from a populated, already-resolved wheelhouse."""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import re
import sys
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path

try:
    from pip._vendor.packaging.requirements import InvalidRequirement, Requirement
except ImportError as exc:  # pragma: no cover - the BAT bootstraps pinned pip first
    raise RuntimeError("Pinned pip with vendored packaging is required") from exc


NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class WheelhouseLockError(RuntimeError):
    """Raised when the source lock and wheelhouse cannot be matched exactly."""


@dataclass(frozen=True)
class RequirementPin:
    name: str
    version: str


def canonicalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def logical_requirements(path: Path) -> list[str]:
    records: list[str] = []
    current = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        continued = line.endswith("\\")
        if continued:
            line = line[:-1].rstrip()
        current = f"{current} {line}".strip()
        if not continued:
            records.append(current)
            current = ""
    if current:
        raise WheelhouseLockError(f"Unterminated requirement continuation: {path}")
    return records


def direct_wheel_version(url: str) -> str:
    filename = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name
    if not filename.lower().endswith(".whl"):
        raise WheelhouseLockError(
            f"Direct requirement is not a wheel and cannot be normalized offline: {url}"
        )
    parts = filename[:-4].split("-")
    if len(parts) < 5 or not parts[1]:
        raise WheelhouseLockError(f"Invalid wheel URL in source lock: {url}")
    return parts[1]


def read_source_pins(path: Path) -> dict[str, RequirementPin]:
    result: dict[str, RequirementPin] = {}
    for record in logical_requirements(path):
        requirement_text = re.sub(r"\s+--hash=\S+", "", record).strip()
        try:
            requirement = Requirement(requirement_text)
        except InvalidRequirement as exc:
            raise WheelhouseLockError(
                f"Unsupported requirement in compiled source lock: {record}"
            ) from exc
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        name = requirement.name
        if requirement.url:
            version = direct_wheel_version(requirement.url)
        else:
            specifiers = list(requirement.specifier)
            if (
                len(specifiers) != 1
                or specifiers[0].operator != "=="
                or specifiers[0].version.endswith(".*")
            ):
                raise WheelhouseLockError(
                    f"Requirement is not pinned exactly in source lock: {record}"
                )
            version = specifiers[0].version
        key = canonicalize_name(name)
        pin = RequirementPin(name=key, version=version)
        previous = result.get(key)
        if previous is not None and previous != pin:
            raise WheelhouseLockError(
                f"Conflicting source-lock pins for {key}: "
                f"{previous.version} and {version}"
            )
        result[key] = pin
    if not result:
        raise WheelhouseLockError(f"Source lock has no requirements: {path}")
    return result


def wheel_identity(path: Path) -> RequirementPin:
    try:
        with zipfile.ZipFile(path) as archive:
            metadata_names = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_names) != 1:
                raise WheelhouseLockError(
                    f"Wheel must contain exactly one METADATA file: {path}"
                )
            metadata = email.parser.BytesParser().parsebytes(
                archive.read(metadata_names[0])
            )
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise WheelhouseLockError(f"Could not inspect wheel {path}: {exc}") from exc
    name = metadata.get("Name", "").strip()
    version = metadata.get("Version", "").strip()
    if not NAME_RE.fullmatch(name) or not version or any(char.isspace() for char in version):
        raise WheelhouseLockError(f"Invalid Name/Version metadata in wheel: {path}")
    return RequirementPin(name=canonicalize_name(name), version=version)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_lock(source_lock: Path, wheelhouse: Path, output: Path) -> int:
    pins = read_source_pins(source_lock)
    available: dict[str, dict[str, list[Path]]] = {}
    wheels = sorted(wheelhouse.glob("*.whl"), key=lambda item: item.name.lower())
    if not wheels:
        raise WheelhouseLockError(f"Wheelhouse contains no wheels: {wheelhouse}")
    for wheel in wheels:
        identity = wheel_identity(wheel)
        available.setdefault(identity.name, {}).setdefault(identity.version, []).append(
            wheel
        )

    missing = [
        f"{pin.name}=={pin.version}"
        for pin in pins.values()
        if pin.version not in available.get(pin.name, {})
    ]
    if missing:
        preview = ", ".join(sorted(missing)[:20])
        suffix = "" if len(missing) <= 20 else f" (+{len(missing) - 20} more)"
        raise WheelhouseLockError(
            f"Wheelhouse is missing {len(missing)} locked distributions: {preview}{suffix}"
        )

    duplicates = []
    selected: list[tuple[RequirementPin, Path]] = []
    for pin in sorted(pins.values(), key=lambda item: (item.name, item.version)):
        matches = available[pin.name][pin.version]
        if len(matches) != 1:
            duplicates.append(
                f"{pin.name}=={pin.version}: "
                + ", ".join(path.name for path in matches)
            )
        else:
            selected.append((pin, matches[0]))
    if duplicates:
        preview = "; ".join(duplicates[:10])
        suffix = "" if len(duplicates) <= 10 else f" (+{len(duplicates) - 10} more)"
        raise WheelhouseLockError(
            "Wheelhouse has ambiguous wheels for locked distributions: "
            f"{preview}{suffix}"
        )

    body = "".join(
        f"{pin.name}=={pin.version} \\\n"
        f"    --hash=sha256:{sha256_file(wheel)}\n"
        for pin, wheel in selected
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.name}.tmp")
    temporary.write_text(body, encoding="utf-8", newline="\n")
    temporary.replace(output)
    return len(pins)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", required=True, type=Path)
    parser.add_argument("--wheelhouse", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        count = create_lock(args.requirements, args.wheelhouse, args.output)
    except (OSError, UnicodeError, WheelhouseLockError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    print(f"[OK] Hash-locked {count} distributions to local wheels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
