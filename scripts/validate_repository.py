#!/usr/bin/env python3
"""Static acceptance checks for the modular portable repository."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "modules"
EXPECTED = {
    "mysql": "8.0.40",
    "elasticsearch": "8.11.3",
    "silo": "2026-08-06T00-00-00Z",
    "valkey": "8.1.6",
    "llama-cpp": "b10786",
    "paddleocr": "3.3.1+3.7.0+3.7.2",
    "ragflow": "0.27.1",
    "web": "0.27.1+caddy-2.11.4",
}
EXPECTED_ARTIFACTS = {
    "uv-x86_64-pc-windows-msvc.zip",
    "cpython-3.13.15+20260901-x86_64-pc-windows-msvc-install_only.tar.gz",
    "cpython-3.11.16+20260901-x86_64-pc-windows-msvc-install_only.tar.gz",
    "node-v24.20.0-win-x64.zip",
    "MinGit-2.55.0.5-64-bit.zip",
    "ragflow-0.27.1.zip",
    "datrie-0.8.3-cp313-cp313-win_amd64.whl",
    "paddlepaddle_gpu-3.3.1-cp311-cp311-win_amd64.whl",
    "PP-DocLayout-L_infer.tar",
    "PP-DocBlockLayout_infer.tar",
    "PP-OCRv6_medium_det_infer.tar",
    "eslav_PP-OCRv5_mobile_rec_infer.tar",
    "SLANet_plus_infer.tar",
    "dejavu-sans-ttf-2.37.zip",
    "VC_redist.x64.exe",
    "mysql-8.0.40-winx64.zip",
    "elasticsearch-8.11.3-windows-x86_64.zip",
    "silo_20260806000000.0.0_windows_amd64.tar.gz",
    "Valkey-8.1.6-Windows-x64-msys2.zip",
    "caddy_2.11.4_windows_amd64.zip",
    "llama-b10786-bin-win-vulkan-x64.zip",
}
REQUIRED = (
    "module.json",
    "README.md",
    "PREPARE-ONLINE.bat",
    "MODULE.bat",
    "control.ps1",
    "src/prepare.ps1",
    "lib/runtime.ps1",
    "_src/.gitkeep",
    "prepared/.gitkeep",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate() -> list[str]:
    errors: list[str] = []
    actual = {path.name for path in MODULES.iterdir() if path.is_dir()}
    if actual != set(EXPECTED):
        errors.append(f"module set: expected {sorted(EXPECTED)}, found {sorted(actual)}")
    manifests: dict[str, dict] = {}
    ports: dict[int, str] = {}
    prepare_hashes: set[str] = set()
    runtime_hashes: set[str] = set()
    artifacts: set[str] = set()
    for name, version in EXPECTED.items():
        root = MODULES / name
        for relative in REQUIRED:
            if not (root / relative).is_file():
                errors.append(f"{name}: missing {relative}")
        try:
            manifest = json.loads((root / "module.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{name}: invalid manifest: {exc}")
            continue
        manifests[name] = manifest
        if manifest.get("schema") != 1 or manifest.get("name") != name:
            errors.append(f"{name}: invalid identity/schema")
        if manifest.get("version") != version:
            errors.append(f"{name}: version changed: {manifest.get('version')} != {version}")
        for artifact in manifest.get("artifacts", []):
            artifacts.add(str(artifact.get("file", "")))
            for key in ("file", "url", "kind", "target", "key"):
                if not artifact.get(key):
                    errors.append(f"{name}: artifact without {key}")
            if not str(artifact.get("url", "")).startswith("https://"):
                errors.append(f"{name}: non-HTTPS artifact URL")
        for label, port in manifest.get("ports", {}).items():
            owner = ports.setdefault(int(port), f"{name}.{label}")
            if owner != f"{name}.{label}":
                errors.append(f"port {port} collision: {owner}, {name}.{label}")
        prepare_hashes.add(digest(root / "src/prepare.ps1"))
        runtime_hashes.add(digest(root / "lib/runtime.ps1"))
        control = (root / "control.ps1").read_text(encoding="utf-8")
        if "verify-payload" not in control:
            errors.append(f"{name}: controller has no verify-payload command")
    if len(prepare_hashes) != 1:
        errors.append("module-local prepare engines diverged")
    if len(runtime_hashes) != 1:
        errors.append("module-local runtime engines diverged")
    if artifacts != EXPECTED_ARTIFACTS:
        errors.append(
            f"fixed artifact set changed: missing={sorted(EXPECTED_ARTIFACTS-artifacts)}, "
            f"added={sorted(artifacts-EXPECTED_ARTIFACTS)}"
        )
    prepare_text = (MODULES / "mysql" / "src/prepare.ps1").read_text(encoding="utf-8")
    if "26.02" not in prepare_text or "2602" not in prepare_text:
        errors.append("7-Zip 26.02 bootstrap pins changed")
    for name, manifest in manifests.items():
        for dependency in manifest.get("runtime_dependencies", []):
            if dependency not in EXPECTED or dependency == name:
                errors.append(f"{name}: invalid dependency {dependency}")
    for obsolete in ("1.PREPARE-ONLINE.bat", "_src/project", "app"):
        if (ROOT / obsolete).exists():
            errors.append(f"obsolete monolithic path remains: {obsolete}")
    return errors


if __name__ == "__main__":
    failures = validate()
    if failures:
        print("\n".join(f"ERROR: {item}" for item in failures))
        raise SystemExit(1)
    print("OK: 8 independent modules, manifests, ports and local engines validated")
