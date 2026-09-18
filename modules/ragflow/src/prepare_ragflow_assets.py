#!/usr/bin/env python3
"""Fetch platform-neutral assets used by RAGFlow without content sealing."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


RAGFLOW_VERSION = "0.27.1"
RECORD_SCHEMA_VERSION = 3
DOWNLOAD_ATTEMPTS = 4
DOWNLOAD_TIMEOUT_SECONDS = 300
DOWNLOAD_BACKOFF_SECONDS = (2, 5, 10)
RETRYABLE_HTTP_CODES = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Asset:
    relative_path: str
    url: str
    copy_from: str | None = None


HF_SNAPSHOTS: dict[str, dict[str, object]] = {
    "InfiniFlow/deepdoc": {
        "revision": "de0e793dc6d744406c96dabd688ccc969f41b443",
        "files": {
            "det.onnx": None,
            "layout.laws.onnx": None,
            "layout.manual.onnx": "layout.laws.onnx",
            "layout.onnx": "layout.laws.onnx",
            "layout.paper.onnx": "layout.laws.onnx",
            "ocr.res": None,
            "rec.onnx": None,
            "tsr.onnx": None,
        },
    },
    "InfiniFlow/text_concat_xgb_v1.0": {
        "revision": "722ed09a54f23f14fe0279ce6b74ce18e1960f54",
        "files": {"updown_concat_xgb.model": None},
    },
}

STANDALONE_ASSETS = (
    Asset(
        "ragflow_deps/cl100k_base.tiktoken",
        "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken",
    ),
    Asset(
        "tika-server-standard-3.3.0.jar",
        "https://repo1.maven.org/maven2/org/apache/tika/tika-server-standard/"
        "3.3.0/tika-server-standard-3.3.0.jar",
    ),
)

NLTK_REPOSITORY = "nltk/nltk_data"
NLTK_REVISION = "550b6625bcef1f2abff2ff770a5a0d272c9c6b2a"
NLTK_ASSETS = tuple(
    Asset(
        relative,
        f"https://raw.githubusercontent.com/{NLTK_REPOSITORY}/{NLTK_REVISION}/"
        f"packages/{relative}",
    )
    for relative in (
        "corpora/wordnet.zip",
        "tokenizers/punkt.zip",
        "tokenizers/punkt_tab.zip",
    )
)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp",
            dir=path.parent, delete=False,
        ) as output:
            temporary_name = output.name
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        temporary = Path(temporary_name)
        if old_mode is not None:
            os.chmod(temporary, old_mode)
        os.replace(temporary, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def asset_present(path: Path) -> bool:
    if path.is_symlink():
        raise RuntimeError(f"Portable asset must not be a symlink: {path}")
    if not path.exists():
        return False
    if not path.is_file():
        raise RuntimeError(f"Asset path is not a regular file: {path}")
    print(f"[OK  ] {path.name} is present", flush=True)
    return True


def retryable_download_error(error: BaseException) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in RETRYABLE_HTTP_CODES
    return isinstance(
        error,
        (TimeoutError, ConnectionError, http.client.IncompleteRead, urllib.error.URLError),
    )


def download_asset(asset: Asset, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{destination.name}.", suffix=".part",
                dir=destination.parent, delete=False,
            ) as output:
                temporary_name = output.name
                request = urllib.request.Request(
                    asset.url, headers={"User-Agent": "local_llm-ragflow-assets/3"}
                )
                print(
                    f"[GET ] {asset.url} (attempt {attempt}/{DOWNLOAD_ATTEMPTS})",
                    flush=True,
                )
                with urllib.request.urlopen(
                    request, timeout=DOWNLOAD_TIMEOUT_SECONDS
                ) as response:
                    shutil.copyfileobj(response, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_name, destination)
            temporary_name = None
            return
        except Exception as error:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
            if not retryable_download_error(error) or attempt >= DOWNLOAD_ATTEMPTS:
                raise RuntimeError(
                    f"Failed to download {asset.relative_path} from {asset.url}: "
                    f"{type(error).__name__}: {error}"
                ) from error
            delay = DOWNLOAD_BACKOFF_SECONDS[
                min(attempt - 1, len(DOWNLOAD_BACKOFF_SECONDS) - 1)
            ]
            print(f"[WARN] Download failed; retrying in {delay}s", flush=True)
            time.sleep(delay)


def manual_asset_candidates(asset: Asset, manual_assets_dir: Path | None) -> list[Path]:
    if manual_assets_dir is None:
        return []
    relative = Path(asset.relative_path)
    return [manual_assets_dir / relative, manual_assets_dir / relative.name]


def copy_asset(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise RuntimeError(f"Manual asset is missing or unsafe: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{destination.name}.", suffix=".part",
            dir=destination.parent, delete=False,
        ) as output:
            temporary_name = output.name
            with source.open("rb") as input_stream:
                shutil.copyfileobj(input_stream, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
        print(f"[COPY] {source.name} -> {destination.name}", flush=True)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def ensure_assets(
    root: Path,
    assets: list[Asset],
    verify_only: bool,
    manual_assets_dir: Path | None = None,
) -> None:
    for asset in assets:
        destination = root / Path(asset.relative_path)
        if asset_present(destination):
            continue
        if verify_only:
            raise RuntimeError(f"Required asset is missing in verify-only mode: {destination}")

        if asset.copy_from is not None:
            alias_source = root / asset.copy_from
            if asset_present(alias_source):
                copy_asset(alias_source, destination)
                continue

        manual_source = next(
            (candidate for candidate in manual_asset_candidates(asset, manual_assets_dir)
             if candidate.is_file() and not candidate.is_symlink()),
            None,
        )
        if manual_source is not None:
            copy_asset(manual_source, destination)
        else:
            download_asset(asset, destination)
        if not asset_present(destination):  # pragma: no cover - defensive
            raise RuntimeError(f"Asset disappeared after installation: {destination}")


def make_huggingface_assets() -> list[Asset]:
    assets: list[Asset] = []
    for repository, snapshot in HF_SNAPSHOTS.items():
        revision = str(snapshot["revision"])
        files = snapshot["files"]
        if not isinstance(files, dict):
            raise RuntimeError(f"Invalid embedded HF manifest for {repository}")
        for filename, copy_from in files.items():
            quoted = urllib.parse.quote(str(filename), safe="")
            url = (
                f"https://huggingface.co/{repository}/resolve/{revision}/"
                f"{quoted}?download=true"
            )
            assets.append(
                Asset(str(filename), url, str(copy_from) if copy_from else None)
            )
    return assets


def check_ragflow_version(ragflow_dir: Path) -> None:
    pyproject = ragflow_dir / "pyproject.toml"
    if not pyproject.is_file():
        raise RuntimeError(f"RAGFlow pyproject.toml is missing: {pyproject}")
    try:
        version = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    except Exception as exc:
        raise RuntimeError(f"Cannot read RAGFlow version from {pyproject}: {exc}") from exc
    if version != RAGFLOW_VERSION:
        raise RuntimeError(f"Expected RAGFlow {RAGFLOW_VERSION}, found {version!r}")


def check_nltk_layout(nltk_dir: Path) -> None:
    existing_nltk_data = os.environ.get("NLTK_DATA", "")
    os.environ["NLTK_DATA"] = os.pathsep.join(
        [str(nltk_dir)] + ([existing_nltk_data] if existing_nltk_data else [])
    )
    try:
        import nltk

        for resource in (
            "corpora/wordnet/", "tokenizers/punkt/", "tokenizers/punkt_tab/"
        ):
            nltk.data.find(resource, paths=[str(nltk_dir)])
    except Exception as exc:
        raise RuntimeError(f"NLTK assets are not usable from {nltk_dir}: {exc}") from exc
    print("[OK  ] NLTK WordNet, Punkt and Punkt Tab are usable offline", flush=True)


def expected_record() -> dict[str, object]:
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "helper": "prepare_ragflow_assets.py",
        "ragflow_version": RAGFLOW_VERSION,
        "policy": "presence and functional checks only; no content hashes or size seals",
        "huggingface": {
            repository: {"revision": snapshot["revision"], "files": snapshot["files"]}
            for repository, snapshot in HF_SNAPSHOTS.items()
        },
        "standalone": {
            asset.relative_path: asset.url for asset in STANDALONE_ASSETS
        },
        "nltk": {
            "repository": NLTK_REPOSITORY,
            "revision": NLTK_REVISION,
            "files": {asset.relative_path: asset.url for asset in NLTK_ASSETS},
        },
    }


def write_record(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError(f"Asset record must not be a symlink: {path}")
    payload = json.dumps(
        expected_record(), ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    if not path.exists() or path.read_bytes() != payload:
        atomic_write(path, payload)
    print(f"[OK  ] Asset provenance record written: {path}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare RAGFlow runtime assets")
    parser.add_argument("--ragflow-dir", required=True, type=Path)
    parser.add_argument("--nltk-dir", required=True, type=Path)
    parser.add_argument("--record", required=True, type=Path)
    parser.add_argument("--verify-only", action="store_true", help="perform no downloads")
    parser.add_argument("--manual-assets-dir", type=Path)
    args = parser.parse_args()

    ragflow_dir = args.ragflow_dir.resolve()
    nltk_dir = args.nltk_dir.resolve()
    manual_assets_dir = (
        args.manual_assets_dir.resolve() if args.manual_assets_dir is not None else None
    )

    check_ragflow_version(ragflow_dir)
    print("[STEP] Prepare Hugging Face runtime models", flush=True)
    ensure_assets(
        ragflow_dir / "rag/res/deepdoc",
        make_huggingface_assets(),
        args.verify_only,
        manual_assets_dir,
    )
    print("[STEP] Prepare Tiktoken and Tika files", flush=True)
    ensure_assets(
        ragflow_dir, list(STANDALONE_ASSETS), args.verify_only, manual_assets_dir
    )
    print("[STEP] Prepare NLTK data", flush=True)
    ensure_assets(nltk_dir, list(NLTK_ASSETS), args.verify_only, manual_assets_dir)
    check_nltk_layout(nltk_dir)
    if not args.verify_only:
        write_record(args.record.resolve())
    print("[OK  ] RAGFlow asset preparation completed", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
