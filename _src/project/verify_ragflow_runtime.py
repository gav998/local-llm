#!/usr/bin/env python3
"""Fail-fast smoke tests for the prepared native-Windows RAGFlow runtime."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import importlib.metadata
import importlib.util
import math
import os
import platform
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


RAGFLOW_VERSION = "0.27.1"
PYTHON_VERSION = "3.13.15"
TASK_HANDLER_RELATIVE = Path(
    "rag/svr/task_executor_refactor/task_handler.py"
)
TASK_HANDLER_PATCHED_SHA256 = (
    "9af7cd2f1dcdb0febe41be85e6c9adf3239057f6453137e37ea7b66f9d04b9f0"
)
XGBOOST_MODEL_RELATIVE = Path("rag/res/deepdoc/updown_concat_xgb.model")
XGBOOST_MODEL_SIZE = 5_906_150
XGBOOST_MODEL_SHA256 = (
    "50516159cd0aab5f3499e1edccffdf1d6141f5ae513fdba003a18cbefa823f62"
)
EXPECTED_DISTRIBUTIONS = {
    "numpy": "2.3.5",
    "xgboost": "2.1.4",
    "infinity-sdk": "0.7.3",
    "datrie": "0.8.3",
    "crawl4ai": "0.9.2",
    "agentrun-sdk": "0.0.51",
}
INFINITY_NUMPY_REQUIREMENT = "numpy>=2,<2.4"
DATRIE_DIST_INFO = "datrie-0.8.3.dist-info"


@dataclass(frozen=True)
class RemovedRequirementMetadata:
    distribution: str
    dist_info: str
    removed_requirement: bytes
    patched_size: int
    patched_sha256: str
    patched_record_digest: str

    @property
    def metadata_entry(self) -> str:
        return f"{self.dist_info}/METADATA"

    @property
    def record_entry(self) -> str:
        return f"{self.dist_info}/RECORD"


EXCLUDED_REQUIREMENT_METADATA = (
    RemovedRequirementMetadata(
        distribution="crawl4ai",
        dist_info="crawl4ai-0.9.2.dist-info",
        removed_requirement=b"Requires-Dist: unclecode-litellm==1.81.13\n",
        patched_size=58_641,
        patched_sha256=(
            "013b49b1d5d0d96f45dca3eeef756fdba2c5cb65bdd5f86e74a6344366ecbe5b"
        ),
        patched_record_digest="ATtJsdXQ2W9F3KPu73Vv26LFy2W91fhudKY0Q2bsvls",
    ),
    RemovedRequirementMetadata(
        distribution="agentrun-sdk",
        dist_info="agentrun_sdk-0.0.51.dist-info",
        removed_requirement=b"Requires-Dist: agentrun-mem0ai>=0.0.10\n",
        patched_size=11_716,
        patched_sha256=(
            "656c78f819c4008bcc76ca06ebdb364844b03017ac24b153d173f79d10326d15"
        ),
        patched_record_digest="ZWx4-BnEAIvMdsoG69s2SESwMBesJLFT0XP3nRAybRU",
    ),
)
CL100K_RELATIVE = Path("ragflow_deps/cl100k_base.tiktoken")
# tiktoken names the cache file with SHA1(URL).  Do not confuse this with
# 6494e42d..., which is the SHA1 of the table contents.
CL100K_CACHE_NAME = "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
CL100K_SIZE = 1_681_126
CL100K_SHA256 = (
    "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_inside(path: Path, root: Path, description: str) -> None:
    try:
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise RuntimeError(
            f"{description} was imported outside the prepared RAGFlow tree: {path}"
        ) from exc


def check_python() -> str:
    if platform.python_implementation() != "CPython":
        raise RuntimeError(
            f"Expected CPython, found {platform.python_implementation()}"
        )
    if platform.python_version() != PYTHON_VERSION:
        raise RuntimeError(
            f"This prepared runtime requires CPython {PYTHON_VERSION}; found "
            f"{sys.version.split()[0]} at {sys.executable}"
        )
    return f"CPython {sys.version.split()[0]}"


def check_ragflow_source(ragflow_dir: Path) -> str:
    pyproject = ragflow_dir / "pyproject.toml"
    if not pyproject.is_file():
        raise RuntimeError(f"Missing RAGFlow pyproject.toml: {pyproject}")
    try:
        version = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
            "version"
        ]
    except Exception as exc:
        raise RuntimeError(f"Cannot parse {pyproject}: {exc}") from exc
    if version != RAGFLOW_VERSION:
        raise RuntimeError(
            f"Expected RAGFlow {RAGFLOW_VERSION}, found {version!r} in {pyproject}"
        )

    task_handler = ragflow_dir / TASK_HANDLER_RELATIVE
    if not task_handler.is_file():
        raise RuntimeError(f"Missing patched task handler: {task_handler}")
    digest = sha256_file(task_handler)
    if digest != TASK_HANDLER_PATCHED_SHA256:
        raise RuntimeError(
            f"RAGFlow task handler is not the audited Windows patch: expected "
            f"SHA256 {TASK_HANDLER_PATCHED_SHA256}, found {digest}"
        )
    return f"RAGFlow {version}; patched task handler {digest[:12]}..."


def verify_removed_requirement_metadata(
    distribution: importlib.metadata.Distribution,
    repair: RemovedRequirementMetadata,
) -> None:
    files = distribution.files
    if files is None:
        raise RuntimeError(f"{repair.distribution} has no installed RECORD")
    metadata_entries = [
        item
        for item in files
        if item.name == "METADATA" and item.parent.name.endswith(".dist-info")
    ]
    if len(metadata_entries) != 1:
        raise RuntimeError(
            f"Could not uniquely locate {repair.distribution} METADATA through "
            f"its RECORD; found {len(metadata_entries)} candidates"
        )
    metadata_entry = metadata_entries[0].as_posix()
    if metadata_entry != repair.metadata_entry:
        raise RuntimeError(
            f"Unexpected {repair.distribution} METADATA path {metadata_entry!r}; "
            f"expected {repair.metadata_entry!r}"
        )
    metadata_path = Path(distribution.locate_file(metadata_entries[0]))
    record_path = metadata_path.with_name("RECORD")
    for description, path in (
        ("METADATA", metadata_path),
        ("RECORD", record_path),
    ):
        if path.is_symlink():
            raise RuntimeError(
                f"{repair.distribution} {description} must not be a symlink: {path}"
            )
        if not path.is_file():
            raise RuntimeError(
                f"{repair.distribution} {description} is missing: {path}"
            )

    metadata = metadata_path.read_bytes()
    digest = hashlib.sha256(metadata).hexdigest()
    if len(metadata) != repair.patched_size or digest != repair.patched_sha256:
        raise RuntimeError(
            f"{repair.distribution} METADATA is not the audited repaired file: "
            f"expected {repair.patched_size} bytes / SHA256 "
            f"{repair.patched_sha256}, found {len(metadata)} bytes / SHA256 {digest}"
        )
    if repair.removed_requirement in metadata:
        raise RuntimeError(
            f"{repair.distribution} retains excluded requirement "
            f"{repair.removed_requirement.rstrip()!r}"
        )

    try:
        rows = list(csv.reader(record_path.read_text(encoding="utf-8").splitlines()))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise RuntimeError(
            f"Cannot parse {repair.distribution} RECORD: {exc}"
        ) from exc
    expected_metadata_row = [
        repair.metadata_entry,
        f"sha256={repair.patched_record_digest}",
        str(repair.patched_size),
    ]
    metadata_rows = [row for row in rows if row and row[0] == repair.metadata_entry]
    if metadata_rows != [expected_metadata_row]:
        raise RuntimeError(
            f"Unexpected {repair.distribution} METADATA RECORD rows: "
            f"{metadata_rows!r}; expected {[expected_metadata_row]!r}"
        )
    self_rows = [row for row in rows if row and row[0] == repair.record_entry]
    if self_rows != [[repair.record_entry, "", ""]]:
        raise RuntimeError(
            f"Unexpected {repair.distribution} RECORD self rows: {self_rows!r}"
        )


def check_distributions() -> str:
    found: list[str] = []
    resolved: dict[str, importlib.metadata.Distribution] = {}
    for name, expected in EXPECTED_DISTRIBUTIONS.items():
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeError(
                f"Required distribution {name}=={expected} is not installed in "
                f"{sys.executable}"
            ) from exc
        if distribution.version != expected:
            raise RuntimeError(
                f"Expected {name}=={expected}, found {distribution.version}"
            )
        found.append(f"{name}=={expected}")
        resolved[name] = distribution

    infinity = importlib.metadata.distribution("infinity-sdk")
    numpy_requirements = [
        value
        for value in (infinity.metadata.get_all("Requires-Dist") or [])
        if value.lower().startswith("numpy")
    ]
    if numpy_requirements != [INFINITY_NUMPY_REQUIREMENT]:
        raise RuntimeError(
            "infinity-sdk NumPy metadata was not repaired: expected "
            f"{INFINITY_NUMPY_REQUIREMENT!r}, found {numpy_requirements!r}"
        )

    for repair in EXCLUDED_REQUIREMENT_METADATA:
        verify_removed_requirement_metadata(resolved[repair.distribution], repair)

    # Import the two compatibility-sensitive packages, not just their metadata.
    importlib.import_module("infinity")
    importlib.import_module("datrie")

    datrie = resolved["datrie"]
    datrie_files = datrie.files
    if datrie_files is None:
        raise RuntimeError("datrie has no installed RECORD")
    metadata_entries = [
        item
        for item in datrie_files
        if item.name == "METADATA" and item.parent.name.endswith(".dist-info")
    ]
    if len(metadata_entries) != 1:
        raise RuntimeError(
            "Could not uniquely locate datrie dist-info through its RECORD"
        )
    metadata_path = Path(datrie.locate_file(metadata_entries[0]))
    if metadata_path.parent.name != DATRIE_DIST_INFO:
        raise RuntimeError(
            f"Unexpected datrie dist-info directory: {metadata_path.parent.name!r}"
        )
    direct_url = metadata_path.with_name("direct_url.json")
    if direct_url.exists() or direct_url.is_symlink():
        raise RuntimeError(
            f"datrie retains build-bound local-wheel provenance: {direct_url}"
        )
    record_path = metadata_path.with_name("RECORD")
    if not record_path.is_file():
        raise RuntimeError(f"datrie RECORD is missing: {record_path}")
    direct_entry = f"{DATRIE_DIST_INFO}/direct_url.json"
    try:
        rows = list(csv.reader(record_path.read_text(encoding="utf-8").splitlines()))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise RuntimeError(f"Cannot parse datrie RECORD: {exc}") from exc
    if any(row and row[0] == direct_entry for row in rows):
        raise RuntimeError(
            f"datrie RECORD retains the build-bound entry {direct_entry!r}"
        )
    return ", ".join(found)


def configure_ragflow_import(ragflow_dir: Path) -> None:
    # Make the smoke deterministic and prohibit optional model downloads.
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["DOC_ENGINE"] = "elasticsearch"
    root_text = str(ragflow_dir)
    sys.path[:] = [item for item in sys.path if item != root_text]
    sys.path.insert(0, root_text)
    os.chdir(ragflow_dir)

    spec = importlib.util.find_spec("rag")
    if spec is None or spec.origin is None:
        raise RuntimeError(f"Cannot resolve the rag package from {ragflow_dir}")
    require_inside(Path(spec.origin), ragflow_dir, "rag")


def check_task_executor_import(ragflow_dir: Path) -> str:
    graph_module = "rag.graphrag.general.index"
    if graph_module in sys.modules:
        raise RuntimeError(
            f"{graph_module} was imported before the task-executor smoke test"
        )
    try:
        module = importlib.import_module("rag.svr.task_executor")
    except OSError as exc:
        raise RuntimeError(
            "Native dependency loading failed while importing task_executor. "
            "On Windows, verify that the app-local VC runtime (especially "
            f"vcomp140.dll) is beside the portable Python runtime. Original error: {exc}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"Could not import rag.svr.task_executor: {type(exc).__name__}: {exc}"
        ) from exc

    module_file = getattr(module, "__file__", None)
    if not module_file:
        raise RuntimeError("rag.svr.task_executor has no source path")
    require_inside(Path(module_file), ragflow_dir, "rag.svr.task_executor")
    if graph_module in sys.modules:
        raise RuntimeError(
            "The optional GraphRAG implementation was eagerly imported. "
            "The audited lazy-import patch is ineffective."
        )
    return "task_executor imported without loading optional GraphRAG"


def verify_cl100k_file(path: Path, description: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"Missing regular {description}: {path}")
    size = path.stat().st_size
    digest = sha256_file(path)
    if size != CL100K_SIZE or digest != CL100K_SHA256:
        raise RuntimeError(
            f"{description} integrity mismatch: expected {CL100K_SIZE} bytes / "
            f"SHA256 {CL100K_SHA256}, found {size} bytes / SHA256 {digest}"
        )


def check_tiktoken_offline(ragflow_dir: Path) -> str:
    bundled = ragflow_dir / CL100K_RELATIVE
    cached = ragflow_dir / CL100K_CACHE_NAME
    verify_cl100k_file(bundled, "bundled cl100k table")
    if cached.exists() or cached.is_symlink():
        verify_cl100k_file(cached, "RAGFlow cl100k cache")

    try:
        module = importlib.import_module("common.token_utils")
    except Exception as exc:
        raise RuntimeError(
            f"Offline common.token_utils import failed: {type(exc).__name__}: {exc}"
        ) from exc
    module_file = getattr(module, "__file__", None)
    if not module_file:
        raise RuntimeError("common.token_utils has no source path")
    require_inside(Path(module_file), ragflow_dir, "common.token_utils")

    verify_cl100k_file(cached, "materialized RAGFlow cl100k cache")
    count = module.num_tokens_from_string("Привет, мир! Offline tokenization 2026.")
    if not isinstance(count, int) or count <= 0:
        raise RuntimeError(f"cl100k tokenizer returned an invalid token count: {count!r}")
    return f"cl100k loaded from bundled bytes; token_count={count}"


def check_russian_tokenizer(ragflow_dir: Path) -> str:
    try:
        tokenizer_module = importlib.import_module("rag.nlp.rag_tokenizer")
        module_file = getattr(tokenizer_module, "__file__", None)
        if not module_file:
            raise RuntimeError("rag.nlp.rag_tokenizer has no source path")
        require_inside(Path(module_file), ragflow_dir, "rag.nlp.rag_tokenizer")
        tokenizer = tokenizer_module.tokenizer
        tokenizer.set_language("russian")
        coarse = tokenizer.tokenize("Привет, мир! Документ 2026.")
        fine = tokenizer.fine_grained_tokenize(coarse)
    except Exception as exc:
        raise RuntimeError(
            f"Russian tokenizer execution failed: {type(exc).__name__}: {exc}"
        ) from exc

    normalized = str(fine).lower().replace("ё", "е")
    missing = [word for word in ("привет", "мир", "документ", "2026") if word not in normalized]
    if missing:
        raise RuntimeError(
            f"Russian tokenizer omitted expected tokens {missing!r}; output was {fine!r}"
        )
    return f"Russian tokens: {fine!r}"


def check_xgboost_prediction(ragflow_dir: Path) -> str:
    model_path = ragflow_dir / XGBOOST_MODEL_RELATIVE
    if not model_path.is_file():
        raise RuntimeError(f"Missing RAGFlow XGBoost model: {model_path}")
    size = model_path.stat().st_size
    digest = sha256_file(model_path)
    if size != XGBOOST_MODEL_SIZE or digest != XGBOOST_MODEL_SHA256:
        raise RuntimeError(
            f"XGBoost model integrity mismatch for {model_path}: expected "
            f"{XGBOOST_MODEL_SIZE} bytes / {XGBOOST_MODEL_SHA256}, found "
            f"{size} bytes / {digest}"
        )

    try:
        import numpy as np
        import xgboost as xgb

        booster = xgb.Booster(params={"nthread": 1})
        booster.load_model(str(model_path))
        features = booster.num_features()
        if features != 32:
            raise RuntimeError(
                f"Expected the audited 32-feature model, found {features} features"
            )
        matrix = xgb.DMatrix(np.zeros((1, features), dtype=np.float32))
        prediction = np.asarray(booster.predict(matrix)).reshape(-1)
    except OSError as exc:
        raise RuntimeError(
            "XGBoost native library could not load. On Windows, verify the "
            f"app-local VC/OpenMP runtime, including vcomp140.dll: {exc}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"XGBoost model execution failed: {type(exc).__name__}: {exc}"
        ) from exc

    if prediction.size != 1:
        raise RuntimeError(
            f"Expected one XGBoost prediction, received shape {prediction.shape}"
        )
    value = float(prediction[0])
    if not math.isfinite(value):
        raise RuntimeError(f"XGBoost returned a non-finite prediction: {value}")
    return f"32-feature model prediction={value:.12g}"


def run_check(label: str, operation: Callable[[], str]) -> None:
    print(f"[STEP] {label}", flush=True)
    result = operation()
    print(f"[OK  ] {result}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the prepared RAGFlow 0.27.1 Windows runtime"
    )
    parser.add_argument("--ragflow-dir", required=True, type=Path)
    args = parser.parse_args()
    ragflow_dir = args.ragflow_dir.resolve()

    run_check("Portable Python", check_python)
    run_check("Pinned RAGFlow source", lambda: check_ragflow_source(ragflow_dir))
    run_check("Pinned native Python distributions", check_distributions)

    print("[STEP] Configure isolated RAGFlow import path", flush=True)
    configure_ragflow_import(ragflow_dir)
    print(f"[OK  ] Import root: {ragflow_dir}", flush=True)

    run_check(
        "RAGFlow bundled cl100k offline smoke",
        lambda: check_tiktoken_offline(ragflow_dir),
    )
    run_check(
        "RAGFlow task executor lazy-import smoke",
        lambda: check_task_executor_import(ragflow_dir),
    )
    run_check(
        "RAGFlow Russian tokenizer smoke",
        lambda: check_russian_tokenizer(ragflow_dir),
    )
    run_check(
        "RAGFlow XGBoost model prediction",
        lambda: check_xgboost_prediction(ragflow_dir),
    )
    print("[OK  ] RAGFlow runtime verification completed", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
