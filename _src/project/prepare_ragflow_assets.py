#!/usr/bin/env python3
"""Fetch only platform-neutral RAGFlow assets needed by the portable build.

RAGFlow's upstream download_deps.py also downloads Linux DEBs, Linux Chrome,
Linux native libraries and Linux executables. Those are intentionally excluded
from this native Windows build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import nltk
from huggingface_hub import HfApi, snapshot_download


RAGFLOW_VERSION = "0.27.1"
HF_REPOSITORIES = (
    "InfiniFlow/deepdoc",
    "InfiniFlow/text_concat_xgb_v1.0",
)
FILES = {
    "ragflow_deps/cl100k_base.tiktoken": (
        "https://openaipublic.blob.core.windows.net/encodings/"
        "cl100k_base.tiktoken"
    ),
    "ragflow_deps/tika-server-standard-3.3.0.jar": (
        "https://repo1.maven.org/maven2/org/apache/tika/"
        "tika-server-standard/3.3.0/tika-server-standard-3.3.0.jar"
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size:
        print(f"[SKIP] {destination.name}", flush=True)
        return

    temporary = destination.with_name(destination.name + ".part")
    temporary.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "local_llm/1"})
    print(f"[GET ] {url}", flush=True)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            with temporary.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
        if not temporary.stat().st_size:
            raise RuntimeError(f"Downloaded an empty file: {url}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def verify_ragflow(ragflow_dir: Path) -> None:
    pyproject = ragflow_dir / "pyproject.toml"
    if not pyproject.is_file():
        raise RuntimeError(f"RAGFlow pyproject.toml is missing: {pyproject}")
    try:
        import tomllib

        version = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
            "version"
        ]
    except Exception as exc:  # pragma: no cover - diagnostic path
        raise RuntimeError(f"Cannot read RAGFlow version: {exc}") from exc
    if version != RAGFLOW_VERSION:
        raise RuntimeError(
            f"Expected RAGFlow {RAGFLOW_VERSION}, found {version} in {pyproject}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ragflow-dir", required=True, type=Path)
    parser.add_argument("--nltk-dir", required=True, type=Path)
    parser.add_argument("--record", required=True, type=Path)
    args = parser.parse_args()

    ragflow_dir = args.ragflow_dir.resolve()
    nltk_dir = args.nltk_dir.resolve()
    verify_ragflow(ragflow_dir)

    model_dir = ragflow_dir / "rag" / "res" / "deepdoc"
    model_dir.mkdir(parents=True, exist_ok=True)
    api = HfApi()
    revisions: dict[str, str] = {}
    for repository in HF_REPOSITORIES:
        revision = api.model_info(repository).sha
        if not revision:
            raise RuntimeError(f"Could not resolve a commit for {repository}")
        print(f"[HF  ] {repository}@{revision}", flush=True)
        snapshot_download(
            repo_id=repository,
            revision=revision,
            local_dir=model_dir,
        )
        revisions[repository] = revision

    nltk_dir.mkdir(parents=True, exist_ok=True)
    for package in ("wordnet", "punkt", "punkt_tab"):
        print(f"[NLTK] {package}", flush=True)
        if not nltk.download(package, download_dir=str(nltk_dir), quiet=False):
            raise RuntimeError(f"NLTK download failed: {package}")

    file_records: dict[str, dict[str, object]] = {}
    for relative, url in FILES.items():
        destination = ragflow_dir / Path(relative)
        download(url, destination)
        file_records[relative] = {
            "url": url,
            "bytes": destination.stat().st_size,
            "sha256": sha256(destination),
        }

    args.record.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "ragflow_version": RAGFLOW_VERSION,
        "huggingface_revisions": revisions,
        "files": file_records,
        "nltk_packages": ["wordnet", "punkt", "punkt_tab"],
    }
    args.record.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[OK  ] Asset record: {args.record}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] {error}", file=sys.stderr, flush=True)
        raise

