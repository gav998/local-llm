#!/usr/bin/env python3
"""Apply native-Windows compatibility fixes to an existing RAGFlow tree.

The patcher deliberately uses semantic anchors instead of whole-file hashes or
size seals.  This lets it update a working tree that already contains unrelated
local changes while keeping every patch idempotent.
"""

from __future__ import annotations

import argparse
import csv
import importlib.metadata
import json
import os
import stat
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path


RAGFLOW_VERSION = "0.27.1"
TASK_HANDLER_RELATIVE = Path("rag/svr/task_executor_refactor/task_handler.py")
GRAPH_IMPORT = b"from rag.graphrag.general.index import run_graphrag_for_kb\n"
GRAPH_METHOD_HEADER = (
    b"    async def _run_graphrag(self, embedding_model: LLMBundle) -> None:\n"
    b'        """Run GraphRAG."""\n'
)
GRAPH_LAZY_IMPORT = (
    b"        from rag.graphrag.general.index import run_graphrag_for_kb\n\n"
)

LEIDEN_RELATIVE = Path("rag/graphrag/general/leiden.py")
LEIDEN_IMPORTS = (
    b"from graspologic.partition import hierarchical_leiden\n"
    b"from graspologic.utils import largest_connected_component\n"
)
LEIDEN_ADAPTER_IMPORT = (
    b"from rag.graphrag.general.graphrag_native_adapter import (\n"
    b"    hierarchical_leiden,\n"
    b"    largest_connected_component,\n"
    b")\n"
)
LEIDEN_ADAPTER_SOURCE = Path(__file__).with_name("graphrag_native_adapter.py")
LEIDEN_ADAPTER_RELATIVE = Path("rag/graphrag/general/graphrag_native_adapter.py")

TENANT_LLM_SERVICE_RELATIVE = Path("api/db/services/tenant_llm_service.py")
TENANT_LLM_MAX_LENGTH_LINE = (
    b'        self.max_length = model_config.get("max_tokens") or 8192\n\n'
)
TENANT_LLM_LOCAL_EMBEDDING_LIMIT = (
    b'        self.max_length = model_config.get("max_tokens") or 8192\n'
    b'        if model_config.get("model_type") == LLMType.EMBEDDING.value:\n'
    b'            local_limit = os.environ.get("LOCAL_RAGFLOW_EMBEDDING_MAX_TOKENS", "").strip()\n'
    b"            if local_limit:\n"
    b"                try:\n"
    b"                    self.max_length = min(self.max_length, max(32, int(local_limit)))\n"
    b"                except ValueError:\n"
    b'                    logging.warning("Ignoring invalid LOCAL_RAGFLOW_EMBEDDING_MAX_TOKENS=%r", local_limit)\n'
    b"\n"
)

TIKA_DISTRIBUTION = "tika"
TIKA_VERSION = "2.6.0"
TIKA_SOURCE_ENTRY = "tika/tika.py"
TIKA_IMPORT_ORIGINAL = b"from subprocess import Popen\nfrom subprocess import STDOUT\n"
TIKA_IMPORT_PATCHED = (
    b"from subprocess import Popen\n"
    b"from subprocess import STDOUT\n"
    b"from subprocess import list2cmdline\n"
)
TIKA_COMMAND_ORIGINAL = (
    b"    # setup command string\n"
    b'    cmd_string = ""\n'
    b"    if not config_path:\n"
    b"        cmd_string = '%s %s -cp \"%s\" org.apache.tika.server.core.TikaServerCli --port %s --host %s &' \\\n"
    b"                     % (java_path, java_args, classpath, port, host)\n"
    b"    else:\n"
    b"        cmd_string = '%s %s -cp \"%s\" org.apache.tika.server.core.TikaServerCli --port %s --host %s --config %s &' \\\n"
    b"                     % (java_path, java_args, classpath, port, host, config_path)\n"
)
TIKA_COMMAND_PATCHED = (
    b"    # setup command string\n"
    b'    cmd_string = ""\n'
    b"    java_command = list2cmdline([java_path])\n"
    b'    if java_args:\n'
    b'        java_command = java_command + " " + java_args\n'
    b"    if not config_path:\n"
    b"        cmd_string = '%s -cp \"%s\" org.apache.tika.server.core.TikaServerCli --port %s --host %s &' \\\n"
    b"                     % (java_command, classpath, port, host)\n"
    b"    else:\n"
    b"        cmd_string = '%s -cp \"%s\" org.apache.tika.server.core.TikaServerCli --port %s --host %s --config %s &' \\\n"
    b"                     % (java_command, classpath, port, host, config_path)\n"
)
TIKA_PROBE_ORIGINAL = (
    b'        _ = Popen(java_path, stdout=open(os.devnull, "w"), stderr=open(os.devnull, "w"))\n'
)
TIKA_PROBE_PATCHED = (
    b'        _ = Popen([java_path], stdout=open(os.devnull, "w"), stderr=open(os.devnull, "w"))\n'
)
TIKA_SIGNATURE_PATCHED = (
    b"def checkJarSig(tikaServerJar, jarPath):\n"
    b'    """Allow the editable portable JAR without content verification."""\n'
    b"    return True\n"
)

INFINITY_DISTRIBUTION = "infinity-sdk"
INFINITY_VERSION = "0.7.3"
INFINITY_DIST_INFO = "infinity_sdk-0.7.3.dist-info"
INFINITY_REQUIREMENT_ORIGINAL = b"Requires-Dist: numpy<2.0.0,>=1.26.0\n"
INFINITY_REQUIREMENT_PATCHED = b"Requires-Dist: numpy>=2,<2.4\n"

DATRIE_DISTRIBUTION = "datrie"
DATRIE_VERSION = "0.8.3"
DATRIE_DIST_INFO = "datrie-0.8.3.dist-info"

MOODLE_DISTRIBUTION = "moodlepy"
MOODLE_VERSION = "0.24.1"
MOODLE_DIST_INFO = "moodlepy-0.24.1.dist-info"
MOODLE_REQUIREMENT_ORIGINAL = b"Requires-Dist: attrs (>=22.2.0,<23.0.0)\n"
MOODLE_REQUIREMENT_PATCHED = b"Requires-Dist: attrs (>=23.2.0)\n"


@dataclass(frozen=True)
class RemovedRequirementPatch:
    distribution: str
    version: str
    dist_info: str
    requirement: bytes

    @property
    def metadata_entry(self) -> str:
        return f"{self.dist_info}/METADATA"


EXCLUDED_REQUIREMENT_PATCHES = (
    RemovedRequirementPatch(
        "crawl4ai", "0.9.2", "crawl4ai-0.9.2.dist-info",
        b"Requires-Dist: unclecode-litellm==1.81.13\n",
    ),
    RemovedRequirementPatch(
        "agentrun-sdk", "0.0.51", "agentrun_sdk-0.0.51.dist-info",
        b"Requires-Dist: agentrun-mem0ai>=0.0.10\n",
    ),
)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp",
            dir=path.parent, delete=False,
        ) as stream:
            temporary_name = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary = Path(temporary_name)
        if old_mode is not None:
            os.chmod(temporary, old_mode)
        os.replace(temporary, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def read_regular(path: Path, description: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"{description} is missing or unsafe: {path}")
    return path.read_bytes()


def ragflow_source_version(ragflow_dir: Path) -> str:
    pyproject = ragflow_dir / "pyproject.toml"
    if not pyproject.is_file():
        raise RuntimeError(f"RAGFlow pyproject.toml is missing: {pyproject}")
    try:
        value = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    except Exception as exc:
        raise RuntimeError(f"Cannot read the RAGFlow version from {pyproject}: {exc}") from exc
    if value != RAGFLOW_VERSION:
        raise RuntimeError(
            f"Unsupported RAGFlow source version {value!r}; expected {RAGFLOW_VERSION!r}"
        )
    return value


def patch_task_handler(ragflow_dir: Path) -> Path:
    target = ragflow_dir / TASK_HANDLER_RELATIVE
    data = read_regular(target, "RAGFlow task handler")
    if data.count(GRAPH_LAZY_IMPORT) == 1 and GRAPH_IMPORT not in data:
        print(f"[OK  ] GraphRAG import is already lazy: {TASK_HANDLER_RELATIVE}")
        return target
    if data.count(GRAPH_IMPORT) != 1 or data.count(GRAPH_METHOD_HEADER) != 1:
        raise RuntimeError(f"Cannot find unique GraphRAG patch anchors in {target}")
    patched = data.replace(GRAPH_IMPORT, b"", 1).replace(
        GRAPH_METHOD_HEADER, GRAPH_METHOD_HEADER + GRAPH_LAZY_IMPORT, 1
    )
    atomic_write(target, patched)
    print(f"[OK  ] Made the GraphRAG dependency lazy: {TASK_HANDLER_RELATIVE}")
    return target


def install_graphrag_native_adapter(ragflow_dir: Path) -> tuple[Path, Path]:
    adapter = read_regular(LEIDEN_ADAPTER_SOURCE, "GraphRAG adapter source")
    adapter_target = ragflow_dir / LEIDEN_ADAPTER_RELATIVE
    if adapter_target.is_symlink():
        raise RuntimeError(f"GraphRAG adapter target must not be a symlink: {adapter_target}")
    if not adapter_target.exists():
        atomic_write(adapter_target, adapter)
    elif not adapter_target.is_file():
        raise RuntimeError(f"GraphRAG adapter target is not a file: {adapter_target}")
    else:
        print(f"[OK  ] Kept existing editable GraphRAG adapter: {adapter_target}")

    leiden_target = ragflow_dir / LEIDEN_RELATIVE
    data = read_regular(leiden_target, "RAGFlow Leiden source")
    if data.count(LEIDEN_ADAPTER_IMPORT) == 1 and LEIDEN_IMPORTS not in data:
        print(f"[OK  ] GraphRAG Leiden imports are already adapted: {LEIDEN_RELATIVE}")
        return leiden_target, adapter_target
    if data.count(LEIDEN_IMPORTS) != 1:
        raise RuntimeError(f"Cannot find unique Leiden import patch anchor in {leiden_target}")
    atomic_write(leiden_target, data.replace(LEIDEN_IMPORTS, LEIDEN_ADAPTER_IMPORT, 1))
    print(f"[OK  ] Routed GraphRAG Leiden through graspologic-native: {LEIDEN_RELATIVE}")
    return leiden_target, adapter_target


def patch_local_embedding_context_limit(ragflow_dir: Path) -> Path:
    target = ragflow_dir / TENANT_LLM_SERVICE_RELATIVE
    data = read_regular(target, "RAGFlow tenant LLM service")
    if data.count(TENANT_LLM_LOCAL_EMBEDDING_LIMIT) == 1:
        print(f"[OK  ] Local embedding context limit is already enforced: {target}")
        return target
    if data.count(TENANT_LLM_MAX_LENGTH_LINE) != 1:
        raise RuntimeError(f"Cannot find unique max_length patch anchor in {target}")
    atomic_write(
        target,
        data.replace(TENANT_LLM_MAX_LENGTH_LINE, TENANT_LLM_LOCAL_EMBEDDING_LIMIT, 1),
    )
    print(f"[OK  ] Enforced local embedding context limit: {TENANT_LLM_SERVICE_RELATIVE}")
    return target


def locate_dist_info(distribution_name: str, version: str, expected_name: str) -> Path:
    try:
        distribution = importlib.metadata.distribution(distribution_name)
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError(f"{distribution_name} {version} is not installed") from exc
    if distribution.version != version:
        raise RuntimeError(
            f"Unsupported {distribution_name} version {distribution.version!r}; expected {version!r}"
        )
    dist_info = Path(distribution._path)  # type: ignore[attr-defined]
    if dist_info.name != expected_name or not dist_info.is_dir():
        raise RuntimeError(f"Unexpected {distribution_name} dist-info directory: {dist_info}")
    return dist_info


def parse_record_row(raw_line: bytes, record_path: Path) -> list[str]:
    try:
        rows = list(csv.reader([raw_line.rstrip(b"\r\n").decode("utf-8")]))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise RuntimeError(f"Cannot parse {record_path}: {exc}") from exc
    if len(rows) != 1 or len(rows[0]) != 3:
        raise RuntimeError(f"Malformed wheel RECORD row in {record_path}")
    return rows[0]


def clear_record_entry(record_path: Path, entry: str) -> None:
    lines = record_path.read_bytes().splitlines(keepends=True)
    matches = 0
    output: list[bytes] = []
    for raw_line in lines:
        row = parse_record_row(raw_line, record_path)
        if row[0] != entry:
            output.append(raw_line)
            continue
        matches += 1
        ending = b"\r\n" if raw_line.endswith(b"\r\n") else b"\n"
        output.append(f"{entry},,".encode("utf-8") + ending)
    if matches != 1:
        raise RuntimeError(f"Expected one {entry} row in {record_path}; found {matches}")
    patched = b"".join(output)
    if patched != b"".join(lines):
        atomic_write(record_path, patched)


def patch_tika_windows_java_launch() -> tuple[Path, Path]:
    dist_info = locate_dist_info(TIKA_DISTRIBUTION, TIKA_VERSION, "tika-2.6.0.dist-info")
    site_packages = dist_info.parent
    source_path = site_packages / TIKA_SOURCE_ENTRY
    record_path = dist_info / "RECORD"
    data = read_regular(source_path, "Tika source")
    read_regular(record_path, "Tika RECORD")
    replacements = (
        (TIKA_IMPORT_ORIGINAL, TIKA_IMPORT_PATCHED, "subprocess imports"),
        (TIKA_COMMAND_ORIGINAL, TIKA_COMMAND_PATCHED, "server command"),
        (TIKA_PROBE_ORIGINAL, TIKA_PROBE_PATCHED, "Java probe"),
    )
    changed = False
    for original, patched, label in replacements:
        if data.count(patched) == 1:
            continue
        if data.count(original) != 1:
            raise RuntimeError(f"Cannot find unique Tika {label} patch anchor in {source_path}")
        data = data.replace(original, patched, 1)
        changed = True
    if TIKA_SIGNATURE_PATCHED not in data:
        signature_start = data.find(b"def checkJarSig(")
        next_function = data.find(b"def startServer(", signature_start + 1)
        if signature_start < 0 or next_function < 0:
            raise RuntimeError(
                f"Cannot find the Tika JAR verification function in {source_path}"
            )
        data = (
            data[:signature_start]
            + TIKA_SIGNATURE_PATCHED
            + b"\n"
            + data[next_function:]
        )
        changed = True
    if changed:
        atomic_write(source_path, data)
    clear_record_entry(record_path, TIKA_SOURCE_ENTRY)
    print("[OK  ] Tika Java launcher is portable-path safe")
    return source_path, record_path


def patch_metadata_requirement(
    distribution: str,
    version: str,
    dist_info_name: str,
    original: bytes,
    patched: bytes,
) -> tuple[Path, Path]:
    dist_info = locate_dist_info(distribution, version, dist_info_name)
    metadata_path = dist_info / "METADATA"
    record_path = dist_info / "RECORD"
    data = read_regular(metadata_path, f"{distribution} METADATA")
    read_regular(record_path, f"{distribution} RECORD")
    if data.count(patched) == 1 and original not in data:
        pass
    elif data.count(original) == 1 and patched not in data:
        atomic_write(metadata_path, data.replace(original, patched, 1))
    else:
        raise RuntimeError(f"Cannot find a unique {distribution} requirement patch anchor")
    clear_record_entry(record_path, f"{dist_info_name}/METADATA")
    return metadata_path, record_path


def patch_infinity_metadata() -> tuple[Path, Path]:
    result = patch_metadata_requirement(
        INFINITY_DISTRIBUTION, INFINITY_VERSION, INFINITY_DIST_INFO,
        INFINITY_REQUIREMENT_ORIGINAL, INFINITY_REQUIREMENT_PATCHED,
    )
    print("[OK  ] Repaired infinity-sdk NumPy requirement")
    return result


def patch_moodle_metadata() -> tuple[Path, Path]:
    result = patch_metadata_requirement(
        MOODLE_DISTRIBUTION, MOODLE_VERSION, MOODLE_DIST_INFO,
        MOODLE_REQUIREMENT_ORIGINAL, MOODLE_REQUIREMENT_PATCHED,
    )
    print("[OK  ] Repaired moodlepy attrs requirement")
    return result


def repair_removed_requirement_metadata(patch: RemovedRequirementPatch) -> tuple[Path, Path]:
    dist_info = locate_dist_info(patch.distribution, patch.version, patch.dist_info)
    metadata_path = dist_info / "METADATA"
    record_path = dist_info / "RECORD"
    data = read_regular(metadata_path, f"{patch.distribution} METADATA")
    read_regular(record_path, f"{patch.distribution} RECORD")
    count = data.count(patch.requirement)
    if count > 1:
        raise RuntimeError(f"Excluded {patch.distribution} requirement is duplicated")
    if count == 1:
        atomic_write(metadata_path, data.replace(patch.requirement, b"", 1))
    clear_record_entry(record_path, patch.metadata_entry)
    print(f"[OK  ] Removed excluded dependency from {patch.distribution} metadata")
    return metadata_path, record_path


def repair_excluded_dependency_metadata() -> None:
    for patch in EXCLUDED_REQUIREMENT_PATCHES:
        repair_removed_requirement_metadata(patch)


def remove_datrie_direct_url() -> tuple[Path, Path]:
    dist_info = locate_dist_info(DATRIE_DISTRIBUTION, DATRIE_VERSION, DATRIE_DIST_INFO)
    direct_url_path = dist_info / "direct_url.json"
    record_path = dist_info / "RECORD"
    read_regular(record_path, "datrie RECORD")
    if direct_url_path.is_symlink():
        raise RuntimeError(f"datrie direct_url.json must not be a symlink: {direct_url_path}")
    if direct_url_path.exists() and not direct_url_path.is_file():
        raise RuntimeError(f"datrie direct URL is not a file: {direct_url_path}")

    direct_entry = f"{DATRIE_DIST_INFO}/direct_url.json"
    lines = record_path.read_bytes().splitlines(keepends=True)
    kept = [line for line in lines if parse_record_row(line, record_path)[0] != direct_entry]
    if len(kept) != len(lines):
        atomic_write(record_path, b"".join(kept))
    direct_url_path.unlink(missing_ok=True)
    print("[OK  ] Removed datrie local-wheel direct_url.json provenance")
    return direct_url_path, record_path


def expected_record() -> dict[str, object]:
    return {
        "schema_version": 7,
        "helper": "prepare_ragflow_windows.py",
        "ragflow_version": RAGFLOW_VERSION,
        "policy": "semantic patch anchors; no content hashes or size seals",
        "changes": [
            "lazy GraphRAG import",
            "graspologic-native adapter",
            "local embedding context cap",
            "portable Tika Java launch",
            "compatible Python dependency metadata",
            "removed build-bound datrie provenance",
        ],
    }


def write_record(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError(f"Compatibility record must not be a symlink: {path}")
    payload = json.dumps(
        expected_record(), ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    if not path.exists() or path.read_bytes() != payload:
        atomic_write(path, payload)
    print(f"[OK  ] Compatibility record written: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply RAGFlow 0.27.1 native-Windows compatibility fixes"
    )
    parser.add_argument("--ragflow-dir", required=True, type=Path)
    parser.add_argument("--record", required=True, type=Path)
    args = parser.parse_args()

    ragflow_dir = args.ragflow_dir.resolve()
    print(f"[STEP] Check RAGFlow {RAGFLOW_VERSION} compatibility")
    ragflow_source_version(ragflow_dir)
    print("[STEP] Keep GraphRAG lazy for non-GraphRAG workers")
    patch_task_handler(ragflow_dir)
    print("[STEP] Enable GraphRAG through graspologic-native")
    install_graphrag_native_adapter(ragflow_dir)
    print("[STEP] Cap embedding input length to the local llama.cpp context")
    patch_local_embedding_context_limit(ragflow_dir)
    print("[STEP] Make tika-python Java launch portable-path safe")
    patch_tika_windows_java_launch()
    print("[STEP] Repair Python dependency metadata")
    patch_infinity_metadata()
    patch_moodle_metadata()
    repair_excluded_dependency_metadata()
    print("[STEP] Remove build-bound datrie wheel provenance")
    remove_datrie_direct_url()
    write_record(args.record.resolve())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
