#!/usr/bin/env python3
"""Static acceptance checks for the modular portable repository."""
from __future__ import annotations

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
    "paddleocr": "3.3.1+3.7.0+3.7.2+workbench1",
    "ragflow": "0.27.1+jre21.0.8.9",
    "web": "0.27.1+caddy-2.11.4",
}
EXPECTED_ARTIFACTS = {
    "uv-x86_64-pc-windows-msvc.zip",
    "cpython-3.13.15+20260901-x86_64-pc-windows-msvc-install_only.tar.gz",
    "cpython-3.11.16+20260901-x86_64-pc-windows-msvc-install_only.tar.gz",
    "node-v24.20.0-win-x64.zip",
    "MinGit-2.55.0.5-64-bit.zip",
    "OpenJDK21U-jre_x64_windows_hotspot_21.0.8_9.zip",
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
)


def validate() -> list[str]:
    errors: list[str] = []
    for relative in ("1.PREPARE-ONLINE.bat", "_src/.gitkeep", "prepared/.gitkeep"):
        if not (ROOT / relative).is_file():
            errors.append(f"shared build path is missing: {relative}")
    actual = {path.name for path in MODULES.iterdir() if path.is_dir()}
    if actual != set(EXPECTED):
        errors.append(f"module set: expected {sorted(EXPECTED)}, found {sorted(actual)}")
    manifests: dict[str, dict] = {}
    ports: dict[int, str] = {}
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
        mutable_paths = manifest.get("mutable_paths")
        if not isinstance(mutable_paths, list) or not all(
            isinstance(path, str) and path for path in mutable_paths
        ):
            errors.append(f"{name}: mutable_paths must be an array of non-empty strings")
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
    if artifacts != EXPECTED_ARTIFACTS:
        errors.append(
            f"fixed artifact set changed: missing={sorted(EXPECTED_ARTIFACTS-artifacts)}, "
            f"added={sorted(artifacts-EXPECTED_ARTIFACTS)}"
        )
    prepare_text = (MODULES / "mysql" / "src/prepare.ps1").read_text(encoding="utf-8")
    if "26.02" not in prepare_text or "2602" not in prepare_text:
        errors.append("7-Zip 26.02 bootstrap pins changed")
    if "-myv=1900" not in prepare_text:
        errors.append("module archives are not pinned to 7-Zip 19.00 decoder compatibility")
    for relative in ("EXTRACT-MODULES.bat", "stack/extract.ps1"):
        if not (ROOT / relative).is_file():
            errors.append(f"deployment extractor is missing: {relative}")
    for name, manifest in manifests.items():
        for dependency in manifest.get("runtime_dependencies", []):
            if dependency not in EXPECTED or dependency == name:
                errors.append(f"{name}: invalid dependency {dependency}")
    for name in EXPECTED:
        if (MODULES / name / "prepared").exists():
            errors.append(f"{name}: module-local prepared directory remains")
    if (ROOT / "stack" / "prepared").exists():
        errors.append("stack-local prepared directory remains")
    for obsolete in ("_src/project", "app"):
        if (ROOT / obsolete).exists():
            errors.append(f"obsolete monolithic path remains: {obsolete}")
    return errors


if __name__ == "__main__":
    failures = validate()
    if failures:
        print("\n".join(f"ERROR: {item}" for item in failures))
        raise SystemExit(1)
    print("OK: 8 independent modules, manifests, ports and local engines validated")
