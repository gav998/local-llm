#!/usr/bin/env python3
"""Fetch and verify the platform-neutral assets used by RAGFlow 0.27.1.

Every remote object is pinned by immutable revision, byte size and SHA256.
Existing files are verified on every run.  Unknown or corrupted content is
never overwritten or recorded as trusted; remove it explicitly after review.
When all files are present, the helper performs no network requests, which also
makes an ordinary second run a useful offline verification pass.
"""

from __future__ import annotations

import argparse
import hashlib
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
RECORD_SCHEMA_VERSION = 2
DOWNLOAD_ATTEMPTS = 4
DOWNLOAD_TIMEOUT_SECONDS = 300
DOWNLOAD_BACKOFF_SECONDS = (2, 5, 10)
RETRYABLE_HTTP_CODES = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Asset:
    relative_path: str
    url: str
    size: int
    sha256: str


HF_SNAPSHOTS: dict[str, dict[str, object]] = {
    "InfiniFlow/deepdoc": {
        "revision": "de0e793dc6d744406c96dabd688ccc969f41b443",
        "files": {
            "det.onnx": (
                4_745_517,
                "30a86f5731181461d08021402766601e4302a9b9b9666be8aff402696339cdff",
            ),
            "layout.laws.onnx": (
                75_726_930,
                "de401c03ee30b1c120416dc06f0705237f0c36d3cdb692c9bfefe8a8f98a4b70",
            ),
            "layout.manual.onnx": (
                75_726_930,
                "de401c03ee30b1c120416dc06f0705237f0c36d3cdb692c9bfefe8a8f98a4b70",
            ),
            "layout.onnx": (
                75_726_930,
                "de401c03ee30b1c120416dc06f0705237f0c36d3cdb692c9bfefe8a8f98a4b70",
            ),
            "layout.paper.onnx": (
                75_726_930,
                "de401c03ee30b1c120416dc06f0705237f0c36d3cdb692c9bfefe8a8f98a4b70",
            ),
            "ocr.res": (
                26_249,
                "28b2362ad4ab2dc38769aa72feb535e3a9ddb3fd2a7585a05920e6393b1dc7f7",
            ),
            "rec.onnx": (
                10_826_336,
                "1c7cf60de2afd728d512f4190cf37455092b45f06175365c6fc58d8cd7e2a68b",
            ),
            "tsr.onnx": (
                12_243_033,
                "1585f88015c60209f16a079a26d944afca790ab7022fe7d0574113ccb9a6f9b4",
            ),
        },
    },
    "InfiniFlow/text_concat_xgb_v1.0": {
        "revision": "722ed09a54f23f14fe0279ce6b74ce18e1960f54",
        "files": {
            "updown_concat_xgb.model": (
                5_906_150,
                "50516159cd0aab5f3499e1edccffdf1d6141f5ae513fdba003a18cbefa823f62",
            ),
        },
    },
}

STANDALONE_ASSETS = (
    Asset(
        "ragflow_deps/cl100k_base.tiktoken",
        "https://openaipublic.blob.core.windows.net/encodings/"
        "cl100k_base.tiktoken",
        1_681_126,
        "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
    ),
    Asset(
        "tika-server-standard-3.3.0.jar",
        "https://repo1.maven.org/maven2/org/apache/tika/tika-server-standard/"
        "3.3.0/tika-server-standard-3.3.0.jar",
        81_853_424,
        "2aca63d25f84774d759de6e132ae7f5723e3ee2adf1d51f585658baba1335e9b",
    ),
    Asset(
        "tika-server-standard-3.3.0.jar.md5",
        "https://repo1.maven.org/maven2/org/apache/tika/tika-server-standard/"
        "3.3.0/tika-server-standard-3.3.0.jar.md5",
        32,
        "50ffd4f04d703c55875fe56a758b4c2928d5bf063c7c45ecc0ee5702be1975e9",
    ),
)

NLTK_REPOSITORY = "nltk/nltk_data"
NLTK_REVISION = "550b6625bcef1f2abff2ff770a5a0d272c9c6b2a"
NLTK_ASSETS = (
    Asset(
        "corpora/wordnet.zip",
        f"https://raw.githubusercontent.com/{NLTK_REPOSITORY}/{NLTK_REVISION}/"
        "packages/corpora/wordnet.zip",
        10_775_600,
        "cbda5ea6eef7f36a97a43d4a75f85e07fccbb4f23657d27b4ccbc93e2646ab59",
    ),
    Asset(
        "tokenizers/punkt.zip",
        f"https://raw.githubusercontent.com/{NLTK_REPOSITORY}/{NLTK_REVISION}/"
        "packages/tokenizers/punkt.zip",
        13_905_355,
        "51c3078994aeaf650bfc8e028be4fb42b4a0d177d41c012b6a983979653660ec",
    ),
    Asset(
        "tokenizers/punkt_tab.zip",
        f"https://raw.githubusercontent.com/{NLTK_REPOSITORY}/{NLTK_REVISION}/"
        "packages/tokenizers/punkt_tab.zip",
        4_319_076,
        "e57f64187974277726a3417ca6f181ec5403676c717672eef6a748a7b20e0106",
    ),
)

TIKA_MD5 = b"532cafa9ad4253aac0750183ebd076fa"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def md5_file(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode: int | None = None
    if path.exists():
        old_mode = stat.S_IMODE(path.stat().st_mode)

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
            delete=False,
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


def inspect_asset(path: Path, asset: Asset) -> bool:
    """Return False only for a cleanly missing asset; corruption is fatal."""

    if path.is_symlink():
        raise RuntimeError(
            f"Portable asset must not be a symlink: {path}. Remove it and rerun."
        )
    if not path.exists():
        return False
    if not path.is_file():
        raise RuntimeError(f"Asset path is not a regular file: {path}")

    size = path.stat().st_size
    digest = sha256_file(path)
    if size != asset.size or digest != asset.sha256:
        raise RuntimeError(
            f"Integrity check failed for {path}: expected {asset.size} bytes / "
            f"SHA256 {asset.sha256}, found {size} bytes / SHA256 {digest}. "
            "Remove the untrusted file explicitly and rerun."
        )
    print(f"[OK  ] {path.name}: {size} bytes / {digest[:12]}...", flush=True)
    return True


def verify_temporary(path: Path, asset: Asset) -> None:
    if not path.is_file():
        raise RuntimeError(f"Download did not create a regular file: {path}")
    size = path.stat().st_size
    digest = sha256_file(path)
    if size != asset.size or digest != asset.sha256:
        raise RuntimeError(
            f"Downloaded content failed integrity verification for {asset.url}: "
            f"expected {asset.size} bytes / SHA256 {asset.sha256}, found "
            f"{size} bytes / SHA256 {digest}"
        )


def install_temporary(temporary: Path, destination: Path) -> None:
    os.replace(temporary, destination)


def retryable_download_error(error: BaseException) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in RETRYABLE_HTTP_CODES
    return isinstance(
        error,
        (
            TimeoutError,
            ConnectionError,
            http.client.IncompleteRead,
            urllib.error.URLError,
        ),
    )


def download_failure_message(
    asset: Asset,
    destination: Path,
    manual_assets_dir: Path | None,
    attempt: int,
    error: BaseException,
) -> str:
    manual_hint = (
        f" or to {manual_assets_dir / Path(asset.relative_path).name}"
        if manual_assets_dir is not None
        else ""
    )
    return (
        f"Failed to download {asset.relative_path} from {asset.url} after "
        f"{attempt} attempt(s): {type(error).__name__}: {error}. "
        f"Manually downloading the pinned file to {destination}{manual_hint} "
        "is also valid; "
        "rerun the script afterward and the existing file will be verified "
        "before any network request."
    )


def download_asset(
    asset: Asset, destination: Path, manual_assets_dir: Path | None = None
) -> None:
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
                    asset.url,
                    headers={"User-Agent": "local_llm-ragflow-assets/2"},
                )
                suffix = (
                    ""
                    if DOWNLOAD_ATTEMPTS == 1
                    else f" (attempt {attempt}/{DOWNLOAD_ATTEMPTS})"
                )
                print(f"[GET ] {asset.url}{suffix}", flush=True)
                with urllib.request.urlopen(
                    request, timeout=DOWNLOAD_TIMEOUT_SECONDS
                ) as response:
                    shutil.copyfileobj(response, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            temporary = Path(temporary_name)
            verify_temporary(temporary, asset)
            install_temporary(temporary, destination)
            temporary_name = None
            return
        except Exception as error:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
            if not retryable_download_error(error) or attempt >= DOWNLOAD_ATTEMPTS:
                raise RuntimeError(
                    download_failure_message(
                        asset, destination, manual_assets_dir, attempt, error
                    )
                ) from error
            delay = DOWNLOAD_BACKOFF_SECONDS[
                min(attempt - 1, len(DOWNLOAD_BACKOFF_SECONDS) - 1)
            ]
            print(
                f"[WARN] {type(error).__name__} while downloading "
                f"{asset.relative_path}: {error}. Retrying in {delay}s...",
                flush=True,
            )
            time.sleep(delay)


def manual_asset_candidates(asset: Asset, manual_assets_dir: Path | None) -> list[Path]:
    if manual_assets_dir is None:
        return []
    relative = Path(asset.relative_path)
    candidates = (manual_assets_dir / relative, manual_assets_dir / relative.name)
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(candidate)
    return unique


def find_manual_asset(asset: Asset, manual_assets_dir: Path | None) -> Path | None:
    for candidate in manual_asset_candidates(asset, manual_assets_dir):
        if candidate.exists():
            inspect_asset(candidate, asset)
            return candidate
    return None


def copy_identical_asset(source: Path, asset: Asset, destination: Path) -> None:
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
        temporary = Path(temporary_name)
        verify_temporary(temporary, asset)
        install_temporary(temporary, destination)
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
    valid_by_content: dict[tuple[int, str], Path] = {}
    missing: list[tuple[Asset, Path]] = []

    for asset in assets:
        destination = root / Path(asset.relative_path)
        if inspect_asset(destination, asset):
            valid_by_content.setdefault((asset.size, asset.sha256), destination)
        else:
            missing.append((asset, destination))

    if missing and verify_only:
        paths = ", ".join(str(path) for _, path in missing)
        raise RuntimeError(f"Required assets are missing in verify-only mode: {paths}")

    for asset, destination in missing:
        content_key = (asset.size, asset.sha256)
        source = valid_by_content.get(content_key)
        if source is not None:
            copy_identical_asset(source, asset, destination)
        else:
            manual_source = find_manual_asset(asset, manual_assets_dir)
            if manual_source is not None:
                copy_identical_asset(manual_source, asset, destination)
            else:
                download_asset(asset, destination, manual_assets_dir)
        if not inspect_asset(destination, asset):  # pragma: no cover - defensive
            raise RuntimeError(f"Asset disappeared after installation: {destination}")
        valid_by_content.setdefault(content_key, destination)


def make_huggingface_assets() -> list[Asset]:
    assets: list[Asset] = []
    for repository, snapshot in HF_SNAPSHOTS.items():
        revision = str(snapshot["revision"])
        files = snapshot["files"]
        if not isinstance(files, dict):  # pragma: no cover - constant invariant
            raise RuntimeError(f"Invalid embedded HF manifest for {repository}")
        for filename, details in files.items():
            size, digest = details
            quoted = urllib.parse.quote(str(filename), safe="")
            url = (
                f"https://huggingface.co/{repository}/resolve/{revision}/"
                f"{quoted}?download=true"
            )
            assets.append(
                Asset(str(filename), url, int(size), str(digest))
            )
    return assets


def verify_ragflow(ragflow_dir: Path) -> None:
    pyproject = ragflow_dir / "pyproject.toml"
    if not pyproject.is_file():
        raise RuntimeError(f"RAGFlow pyproject.toml is missing: {pyproject}")
    try:
        version = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
            "version"
        ]
    except Exception as exc:
        raise RuntimeError(f"Cannot read RAGFlow version from {pyproject}: {exc}") from exc
    if version != RAGFLOW_VERSION:
        raise RuntimeError(
            f"Expected RAGFlow {RAGFLOW_VERSION}, found {version!r} in {pyproject}"
        )


def verify_nltk_layout(nltk_dir: Path) -> None:
    try:
        # NLTK 3.10's path-security layer snapshots trusted data roots while
        # importing the package.  Advertise this portable root before import;
        # passing it only to nltk.data.find() is intentionally not sufficient.
        existing_nltk_data = os.environ.get("NLTK_DATA", "")
        roots = [str(nltk_dir)]
        if existing_nltk_data:
            roots.append(existing_nltk_data)
        os.environ["NLTK_DATA"] = os.pathsep.join(roots)
        import nltk

        resources = (
            "corpora/wordnet/",
            "tokenizers/punkt/",
            "tokenizers/punkt_tab/",
        )
        for resource in resources:
            nltk.data.find(resource, paths=[str(nltk_dir)])
    except Exception as exc:
        raise RuntimeError(
            f"Pinned NLTK ZIP files are not usable from {nltk_dir}: {exc}"
        ) from exc
    print("[OK  ] NLTK WordNet, Punkt and Punkt Tab are available offline", flush=True)


def verify_tika_pair(ragflow_dir: Path) -> None:
    # Match the upstream Docker layout.  At runtime TIKA_SERVER_JAR must be a
    # file:/// URI for this root-level JAR; tika finds the adjacent .md5 file.
    jar = ragflow_dir / "tika-server-standard-3.3.0.jar"
    sidecar = jar.with_name(jar.name + ".md5")
    actual_md5 = md5_file(jar).encode("ascii")
    sidecar_bytes = sidecar.read_bytes()
    if actual_md5 != TIKA_MD5 or sidecar_bytes != TIKA_MD5:
        raise RuntimeError(
            "Tika JAR/MD5 pair is inconsistent: expected the canonical 32-byte "
            f"digest {TIKA_MD5.decode('ascii')}, got JAR={actual_md5!r}, "
            f"sidecar={sidecar_bytes!r}"
        )
    print("[OK  ] Tika JAR and canonical .jar.md5 sidecar match", flush=True)


def expected_record() -> dict[str, object]:
    huggingface: dict[str, object] = {}
    for repository, snapshot in HF_SNAPSHOTS.items():
        files = snapshot["files"]
        assert isinstance(files, dict)
        huggingface[repository] = {
            "revision": snapshot["revision"],
            "files": {
                str(name): {"bytes": int(details[0]), "sha256": str(details[1])}
                for name, details in files.items()
            },
        }
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "helper": "prepare_ragflow_assets.py",
        "ragflow_version": RAGFLOW_VERSION,
        "huggingface": huggingface,
        "standalone": {
            asset.relative_path: {
                "url": asset.url,
                "bytes": asset.size,
                "sha256": asset.sha256,
            }
            for asset in STANDALONE_ASSETS
        },
        "nltk": {
            "repository": NLTK_REPOSITORY,
            "revision": NLTK_REVISION,
            "files": {
                asset.relative_path: {
                    "url": asset.url,
                    "bytes": asset.size,
                    "sha256": asset.sha256,
                }
                for asset in NLTK_ASSETS
            },
        },
        "runtime_configuration": {
            "TIKA_SERVER_JAR": (
                "file:///<RAGFLOW_ROOT>/tika-server-standard-3.3.0.jar"
            ),
        },
    }


def canonical_record() -> bytes:
    return json.dumps(
        expected_record(), ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"


def preflight_record(path: Path, verify_only: bool) -> bool:
    if path.is_symlink():
        raise RuntimeError(f"Asset record must not be a symlink: {path}")
    if not path.exists():
        if verify_only:
            raise RuntimeError(f"Asset record is missing in verify-only mode: {path}")
        return False
    if not path.is_file():
        raise RuntimeError(f"Asset record is not a regular file: {path}")
    if path.read_bytes() != canonical_record():
        raise RuntimeError(
            f"Asset record is stale or modified: {path}. Remove it only after "
            "reviewing the changed provenance, then rerun preparation."
        )
    print(f"[OK  ] Asset record provenance verified: {path}", flush=True)
    return True


def write_record(path: Path, already_present: bool) -> None:
    expected = canonical_record()
    if already_present:
        if path.read_bytes() != expected:
            raise RuntimeError(f"Asset record changed during verification: {path}")
        print(f"[OK  ] Asset record unchanged: {path}", flush=True)
        return
    atomic_write(path, expected)
    if path.read_bytes() != expected:
        raise RuntimeError(f"Asset record verification failed after writing: {path}")
    print(f"[OK  ] Asset record written: {path}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare or verify pinned platform-neutral RAGFlow assets"
    )
    parser.add_argument("--ragflow-dir", required=True, type=Path)
    parser.add_argument("--nltk-dir", required=True, type=Path)
    parser.add_argument("--record", required=True, type=Path)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="perform no downloads and require the deterministic record to exist",
    )
    parser.add_argument(
        "--manual-assets-dir",
        type=Path,
        help=(
            "optional directory for manually downloaded pinned assets; files may "
            "use either their manifest relative path or plain file name"
        ),
    )
    args = parser.parse_args()

    ragflow_dir = args.ragflow_dir.resolve()
    nltk_dir = args.nltk_dir.resolve()
    record_path = args.record.resolve()
    manual_assets_dir = (
        args.manual_assets_dir.resolve() if args.manual_assets_dir is not None else None
    )

    print(f"[STEP] Verify RAGFlow {RAGFLOW_VERSION} source", flush=True)
    verify_ragflow(ragflow_dir)
    record_present = preflight_record(record_path, args.verify_only)

    print("[STEP] Verify pinned Hugging Face runtime models", flush=True)
    model_dir = ragflow_dir / "rag/res/deepdoc"
    ensure_assets(
        model_dir, make_huggingface_assets(), args.verify_only, manual_assets_dir
    )

    print("[STEP] Verify pinned Tiktoken and Tika files", flush=True)
    ensure_assets(
        ragflow_dir, list(STANDALONE_ASSETS), args.verify_only, manual_assets_dir
    )
    verify_tika_pair(ragflow_dir)
    print(
        "[INFO] Runtime must set TIKA_SERVER_JAR to the root-level JAR file:/// URI",
        flush=True,
    )

    print("[STEP] Verify pinned NLTK data", flush=True)
    ensure_assets(nltk_dir, list(NLTK_ASSETS), args.verify_only, manual_assets_dir)
    verify_nltk_layout(nltk_dir)

    print("[STEP] Commit deterministic asset provenance", flush=True)
    write_record(record_path, record_present)
    print("[OK  ] RAGFlow asset preparation completed", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
