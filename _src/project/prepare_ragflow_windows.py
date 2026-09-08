#!/usr/bin/env python3
"""Apply the audited native-Windows compatibility fixes for RAGFlow.

This helper is intentionally narrow.  It only accepts the exact RAGFlow
0.27.1 source and exact audited wheel metadata for this portable build,
including infinity-sdk, Crawl4AI, and agentrun-sdk.  Unknown input is never
patched heuristically.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import importlib.metadata
import json
import os
import stat
import sys
import tempfile
import tomllib
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


RAGFLOW_VERSION = "0.27.1"
TASK_HANDLER_RELATIVE = Path(
    "rag/svr/task_executor_refactor/task_handler.py"
)
TASK_HANDLER_ORIGINAL_SHA256 = (
    "6799016e4a4952dd04b220d31985e54fe0dcdc742779c93e127f38ccb5530ce0"
)
TASK_HANDLER_PATCHED_SHA256 = (
    "9af7cd2f1dcdb0febe41be85e6c9adf3239057f6453137e37ea7b66f9d04b9f0"
)

LEIDEN_RELATIVE = Path("rag/graphrag/general/leiden.py")
LEIDEN_ORIGINAL_SHA256 = (
    "705ff97a7c6b19408bb93b3aeb6fdfdff789d5a8775a5d438ca940c8e623374f"
)
LEIDEN_PATCHED_SHA256 = (
    "aba607c77cbf496319f3c7c78ec55bb145d481479fecea89b468c1bb4a64f828"
)
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
LEIDEN_ADAPTER_RELATIVE = Path(
    "rag/graphrag/general/graphrag_native_adapter.py"
)
LEIDEN_ADAPTER_SHA256 = (
    "b833c729a3329a704d39c09dc06002857a99cf72b84956dba99c762d49e0919b"
)

GRAPH_IMPORT = b"from rag.graphrag.general.index import run_graphrag_for_kb\n"
GRAPH_METHOD_HEADER = (
    b"    async def _run_graphrag(self, embedding_model: LLMBundle) -> None:\n"
    b'        """Run GraphRAG."""\n'
)
GRAPH_LAZY_IMPORT = (
    b"        from rag.graphrag.general.index import run_graphrag_for_kb\n\n"
)

INFINITY_DISTRIBUTION = "infinity-sdk"
INFINITY_VERSION = "0.7.3"
INFINITY_DIST_INFO = "infinity_sdk-0.7.3.dist-info"
INFINITY_REQUIREMENT_ORIGINAL = b"Requires-Dist: numpy<2.0.0,>=1.26.0\n"
INFINITY_REQUIREMENT_PATCHED = b"Requires-Dist: numpy>=2,<2.4\n"
INFINITY_METADATA_ORIGINAL_SIZE = 5907
INFINITY_METADATA_ORIGINAL_SHA256 = (
    "4358284f1cbb66f768d828439afe42a648e5d28cdd1837eff0d11db26a55c59b"
)
INFINITY_METADATA_PATCHED_SIZE = 5900
INFINITY_METADATA_PATCHED_SHA256 = (
    "e28f17ab226466cc88927d8e35d385a8a02f63574081518f75e09274080d1406"
)

DATRIE_DISTRIBUTION = "datrie"
DATRIE_VERSION = "0.8.3"
DATRIE_DIST_INFO = "datrie-0.8.3.dist-info"
DATRIE_WHEEL_NAME = "datrie-0.8.3-cp313-cp313-win_amd64.whl"
DATRIE_WHEEL_SHA256 = (
    "76eb11c37919646ccd276a76eed9db2f066fbfb23b577ef32efeacf5387aea0e"
)
DATRIE_HASH_PREFIXES = ("sha256=", "sha256:")

MOODLE_DISTRIBUTION = "moodlepy"
MOODLE_VERSION = "0.24.1"
MOODLE_DIST_INFO = "moodlepy-0.24.1.dist-info"
MOODLE_REQUIREMENT_ORIGINAL = b"Requires-Dist: attrs (>=22.2.0,<23.0.0)\n"
MOODLE_REQUIREMENT_PATCHED = b"Requires-Dist: attrs (>=23.2.0)\n"
MOODLE_METADATA_ORIGINAL_SIZE = 8_344
MOODLE_METADATA_ORIGINAL_SHA256 = (
    "7ad1f8d84a9d8e723c63b358f17ec5cfc55c83154d2c56302245662b742d980d"
)
MOODLE_METADATA_PATCHED_SIZE = 8_336
MOODLE_METADATA_PATCHED_SHA256 = (
    "253e4fffc22de184669efdfafccc6a57a6234760e0cbe4cc75245c99ecce1f59"
)


@dataclass(frozen=True)
class RemovedRequirementPatch:
    distribution: str
    version: str
    dist_info: str
    requirement: bytes
    original_size: int
    original_sha256: str
    patched_size: int
    patched_sha256: str

    @property
    def metadata_entry(self) -> str:
        return f"{self.dist_info}/METADATA"

    @property
    def record_entry(self) -> str:
        return f"{self.dist_info}/RECORD"


CRAWL4AI_EXCLUSION = RemovedRequirementPatch(
    distribution="crawl4ai",
    version="0.9.2",
    dist_info="crawl4ai-0.9.2.dist-info",
    requirement=b"Requires-Dist: unclecode-litellm==1.81.13\n",
    original_size=58_683,
    original_sha256=(
        "48a217301d52cf8a5cf9ee399e7d4cd0dd6456967d95fbcd559e5cecbecfbc6f"
    ),
    patched_size=58_641,
    patched_sha256=(
        "013b49b1d5d0d96f45dca3eeef756fdba2c5cb65bdd5f86e74a6344366ecbe5b"
    ),
)
AGENTRUN_EXCLUSION = RemovedRequirementPatch(
    distribution="agentrun-sdk",
    version="0.0.51",
    dist_info="agentrun_sdk-0.0.51.dist-info",
    requirement=b"Requires-Dist: agentrun-mem0ai>=0.0.10\n",
    original_size=11_755,
    original_sha256=(
        "13b2276e4e747da0e0aa764254eb7c92c450851d1c96d203c608e87e0389b34c"
    ),
    patched_size=11_716,
    patched_sha256=(
        "656c78f819c4008bcc76ca06ebdb364844b03017ac24b153d173f79d10326d15"
    ),
)
EXCLUDED_REQUIREMENT_PATCHES = (CRAWL4AI_EXCLUSION, AGENTRUN_EXCLUSION)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def record_digest(hex_digest: str) -> str:
    raw = bytes.fromhex(hex_digest)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


INFINITY_RECORD_DIGEST_ORIGINAL = record_digest(
    INFINITY_METADATA_ORIGINAL_SHA256
)
INFINITY_RECORD_DIGEST_PATCHED = record_digest(INFINITY_METADATA_PATCHED_SHA256)


def atomic_write(path: Path, data: bytes) -> None:
    """Replace *path* atomically while retaining its current mode when possible."""

    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode: int | None = None
    if path.exists():
        old_mode = stat.S_IMODE(path.stat().st_mode)

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
            delete=False,
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


def ragflow_source_version(ragflow_dir: Path) -> str:
    pyproject = ragflow_dir / "pyproject.toml"
    if not pyproject.is_file():
        raise RuntimeError(f"RAGFlow pyproject.toml is missing: {pyproject}")
    try:
        value = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
            "version"
        ]
    except Exception as exc:
        raise RuntimeError(f"Cannot read the RAGFlow version from {pyproject}: {exc}") from exc
    if value != RAGFLOW_VERSION:
        raise RuntimeError(
            f"Unsupported RAGFlow source version {value!r}; expected "
            f"{RAGFLOW_VERSION!r}. No files were patched."
        )
    return value


def patch_task_handler(ragflow_dir: Path) -> Path:
    target = ragflow_dir / TASK_HANDLER_RELATIVE
    if target.is_symlink():
        raise RuntimeError(f"RAGFlow task handler must not be a symlink: {target}")
    if not target.is_file():
        raise RuntimeError(f"RAGFlow task handler is missing: {target}")

    original = target.read_bytes()
    current_hash = sha256_bytes(original)
    if current_hash == TASK_HANDLER_PATCHED_SHA256:
        print(f"[OK  ] GraphRAG import is already lazy: {TASK_HANDLER_RELATIVE}")
        return target
    if current_hash != TASK_HANDLER_ORIGINAL_SHA256:
        raise RuntimeError(
            f"Refusing to patch unknown RAGFlow source: {target}\n"
            f"Expected SHA256 {TASK_HANDLER_ORIGINAL_SHA256} (original) or "
            f"{TASK_HANDLER_PATCHED_SHA256} (patched), found {current_hash}."
        )
    if original.count(GRAPH_IMPORT) != 1:
        raise RuntimeError("The audited top-level GraphRAG import is not unique")
    if original.count(GRAPH_METHOD_HEADER) != 1:
        raise RuntimeError("The audited _run_graphrag method header is not unique")
    if GRAPH_LAZY_IMPORT in original:
        raise RuntimeError("Unexpected lazy GraphRAG import in original source")

    patched = original.replace(GRAPH_IMPORT, b"", 1)
    patched = patched.replace(
        GRAPH_METHOD_HEADER,
        GRAPH_METHOD_HEADER + GRAPH_LAZY_IMPORT,
        1,
    )
    patched_hash = sha256_bytes(patched)
    if patched_hash != TASK_HANDLER_PATCHED_SHA256:
        raise RuntimeError(
            "Internal patch result did not match the audited SHA256: "
            f"expected {TASK_HANDLER_PATCHED_SHA256}, got {patched_hash}"
        )
    atomic_write(target, patched)
    if sha256_bytes(target.read_bytes()) != TASK_HANDLER_PATCHED_SHA256:
        raise RuntimeError(f"GraphRAG source verification failed after writing {target}")
    print(f"[OK  ] Made the GraphRAG dependency lazy: {TASK_HANDLER_RELATIVE}")
    return target


def install_graphrag_native_adapter(ragflow_dir: Path) -> tuple[Path, Path]:
    """Install the audited adapter and redirect RAGFlow's exact Leiden imports."""

    if LEIDEN_ADAPTER_SOURCE.is_symlink() or not LEIDEN_ADAPTER_SOURCE.is_file():
        raise RuntimeError(
            f"GraphRAG adapter source is missing or unsafe: {LEIDEN_ADAPTER_SOURCE}"
        )
    adapter = LEIDEN_ADAPTER_SOURCE.read_bytes()
    adapter_hash = sha256_bytes(adapter)
    if adapter_hash != LEIDEN_ADAPTER_SHA256:
        raise RuntimeError(
            "GraphRAG adapter source does not match its audited SHA256: "
            f"expected {LEIDEN_ADAPTER_SHA256}, found {adapter_hash}"
        )

    adapter_target = ragflow_dir / LEIDEN_ADAPTER_RELATIVE
    if adapter_target.is_symlink():
        raise RuntimeError(f"GraphRAG adapter target must not be a symlink: {adapter_target}")
    if adapter_target.exists():
        if not adapter_target.is_file():
            raise RuntimeError(f"GraphRAG adapter target is not a file: {adapter_target}")
        current_hash = sha256_bytes(adapter_target.read_bytes())
        if current_hash != LEIDEN_ADAPTER_SHA256:
            raise RuntimeError(
                f"Refusing to replace unknown GraphRAG adapter {adapter_target}: "
                f"found SHA256 {current_hash}"
            )
    else:
        atomic_write(adapter_target, adapter)
    if sha256_bytes(adapter_target.read_bytes()) != LEIDEN_ADAPTER_SHA256:
        raise RuntimeError(f"GraphRAG adapter verification failed: {adapter_target}")

    leiden_target = ragflow_dir / LEIDEN_RELATIVE
    if leiden_target.is_symlink() or not leiden_target.is_file():
        raise RuntimeError(f"RAGFlow Leiden source is missing or unsafe: {leiden_target}")
    original = leiden_target.read_bytes()
    current_hash = sha256_bytes(original)
    if current_hash == LEIDEN_PATCHED_SHA256:
        if original.count(LEIDEN_ADAPTER_IMPORT) != 1:
            raise RuntimeError("Patched Leiden adapter import is not unique")
        print(f"[OK  ] GraphRAG Leiden imports are already adapted: {LEIDEN_RELATIVE}")
        return leiden_target, adapter_target
    if current_hash != LEIDEN_ORIGINAL_SHA256:
        raise RuntimeError(
            f"Refusing to patch unknown RAGFlow Leiden source: {leiden_target}\n"
            f"Expected SHA256 {LEIDEN_ORIGINAL_SHA256} (original) or "
            f"{LEIDEN_PATCHED_SHA256} (patched), found {current_hash}."
        )
    if original.count(LEIDEN_IMPORTS) != 1:
        raise RuntimeError("The audited graspologic Leiden imports are not unique")
    patched = original.replace(LEIDEN_IMPORTS, LEIDEN_ADAPTER_IMPORT, 1)
    patched_hash = sha256_bytes(patched)
    if patched_hash != LEIDEN_PATCHED_SHA256:
        raise RuntimeError(
            "Internal Leiden patch result did not match the audited SHA256: "
            f"expected {LEIDEN_PATCHED_SHA256}, got {patched_hash}"
        )
    atomic_write(leiden_target, patched)
    if sha256_bytes(leiden_target.read_bytes()) != LEIDEN_PATCHED_SHA256:
        raise RuntimeError(f"GraphRAG Leiden verification failed: {leiden_target}")
    print(f"[OK  ] Routed GraphRAG Leiden through graspologic-native: {LEIDEN_RELATIVE}")
    return leiden_target, adapter_target


def locate_infinity_metadata() -> tuple[Path, Path, str]:
    try:
        distribution = importlib.metadata.distribution(INFINITY_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError(
            f"{INFINITY_DISTRIBUTION} {INFINITY_VERSION} is not installed in "
            f"{sys.executable}"
        ) from exc

    if distribution.version != INFINITY_VERSION:
        raise RuntimeError(
            f"Unsupported {INFINITY_DISTRIBUTION} version "
            f"{distribution.version!r}; expected {INFINITY_VERSION!r}"
        )
    files = distribution.files
    if files is None:
        raise RuntimeError(f"{INFINITY_DISTRIBUTION} has no installed RECORD")

    candidates = [
        item
        for item in files
        if item.name == "METADATA" and item.parent.name.endswith(".dist-info")
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            "Could not uniquely locate infinity-sdk METADATA through its RECORD; "
            f"found {len(candidates)} candidates"
        )
    metadata_entry = candidates[0].as_posix()
    metadata_path = Path(distribution.locate_file(candidates[0]))
    if metadata_path.parent.name != INFINITY_DIST_INFO:
        raise RuntimeError(
            f"Unexpected infinity-sdk dist-info directory: {metadata_path.parent.name!r}; "
            f"expected {INFINITY_DIST_INFO!r}"
        )
    record_path = metadata_path.with_name("RECORD")
    if not metadata_path.is_file() or not record_path.is_file():
        raise RuntimeError(
            f"infinity-sdk metadata files are incomplete: {metadata_path}, {record_path}"
        )
    return metadata_path, record_path, metadata_entry


def metadata_state(data: bytes) -> str:
    size = len(data)
    digest = sha256_bytes(data)
    if (
        size == INFINITY_METADATA_ORIGINAL_SIZE
        and digest == INFINITY_METADATA_ORIGINAL_SHA256
    ):
        if data.count(INFINITY_REQUIREMENT_ORIGINAL) != 1:
            raise RuntimeError("Audited infinity-sdk METADATA has an unexpected NumPy line")
        return "original"
    if (
        size == INFINITY_METADATA_PATCHED_SIZE
        and digest == INFINITY_METADATA_PATCHED_SHA256
    ):
        if data.count(INFINITY_REQUIREMENT_PATCHED) != 1:
            raise RuntimeError("Patched infinity-sdk METADATA has an unexpected NumPy line")
        return "patched"
    raise RuntimeError(
        "Refusing to patch unknown infinity-sdk METADATA: "
        f"expected SHA256 {INFINITY_METADATA_ORIGINAL_SHA256} (original) or "
        f"{INFINITY_METADATA_PATCHED_SHA256} (patched), found {digest} ({size} bytes)"
    )


def parse_record_row(raw_line: bytes, record_path: Path) -> list[str]:
    body = raw_line.rstrip(b"\r\n")
    try:
        rows = list(csv.reader([body.decode("utf-8")]))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise RuntimeError(f"Cannot parse {record_path}: {exc}") from exc
    if len(rows) != 1 or len(rows[0]) != 3:
        raise RuntimeError(f"Malformed wheel RECORD row in {record_path}: {body!r}")
    return rows[0]


def inspect_record(
    data: bytes, record_path: Path, metadata_entry: str
) -> tuple[str, list[bytes], int]:
    lines = data.splitlines(keepends=True)
    metadata_indices: list[int] = []
    self_rows = 0
    record_entry = f"{INFINITY_DIST_INFO}/RECORD"
    state: str | None = None

    for index, raw_line in enumerate(lines):
        row = parse_record_row(raw_line, record_path)
        if row[0] == metadata_entry:
            metadata_indices.append(index)
            old = [
                metadata_entry,
                f"sha256={INFINITY_RECORD_DIGEST_ORIGINAL}",
                str(INFINITY_METADATA_ORIGINAL_SIZE),
            ]
            new = [
                metadata_entry,
                f"sha256={INFINITY_RECORD_DIGEST_PATCHED}",
                str(INFINITY_METADATA_PATCHED_SIZE),
            ]
            if row == old:
                state = "original"
            elif row == new:
                state = "patched"
            else:
                raise RuntimeError(
                    f"Unexpected infinity-sdk METADATA row in {record_path}: {row!r}"
                )
        if row[0] == record_entry:
            self_rows += 1
            if row[1:] != ["", ""]:
                raise RuntimeError(f"Malformed self row in {record_path}: {row!r}")

    if len(metadata_indices) != 1 or state is None:
        raise RuntimeError(
            f"Expected exactly one infinity-sdk METADATA row in {record_path}; "
            f"found {len(metadata_indices)}"
        )
    if self_rows != 1:
        raise RuntimeError(
            f"Expected exactly one infinity-sdk RECORD self row in {record_path}; "
            f"found {self_rows}"
        )
    return state, lines, metadata_indices[0]


def patched_record_bytes(lines: list[bytes], metadata_index: int) -> bytes:
    current = lines[metadata_index]
    if current.endswith(b"\r\n"):
        ending = b"\r\n"
    elif current.endswith(b"\n"):
        ending = b"\n"
    elif current.endswith(b"\r"):
        ending = b"\r"
    else:
        ending = b""
    entry = (
        f"{INFINITY_DIST_INFO}/METADATA,"
        f"sha256={INFINITY_RECORD_DIGEST_PATCHED},"
        f"{INFINITY_METADATA_PATCHED_SIZE}"
    ).encode("ascii")
    lines[metadata_index] = entry + ending
    return b"".join(lines)


def patch_infinity_metadata() -> tuple[Path, Path]:
    metadata_path, record_path, metadata_entry = locate_infinity_metadata()
    expected_entry = f"{INFINITY_DIST_INFO}/METADATA"
    if metadata_entry != expected_entry:
        raise RuntimeError(
            f"Unexpected infinity-sdk METADATA RECORD path {metadata_entry!r}; "
            f"expected {expected_entry!r}"
        )

    metadata = metadata_path.read_bytes()
    record = record_path.read_bytes()
    metadata_status = metadata_state(metadata)
    record_status, record_lines, metadata_index = inspect_record(
        record, record_path, metadata_entry
    )

    # A previous process can have stopped between the two atomic replacements.
    # Either audited mixed state is safe to finish; any unknown bytes fail above.
    if metadata_status == "original":
        if metadata.count(INFINITY_REQUIREMENT_ORIGINAL) != 1:
            raise RuntimeError("The infinity-sdk NumPy requirement is not unique")
        patched = metadata.replace(
            INFINITY_REQUIREMENT_ORIGINAL,
            INFINITY_REQUIREMENT_PATCHED,
            1,
        )
        if (
            len(patched) != INFINITY_METADATA_PATCHED_SIZE
            or sha256_bytes(patched) != INFINITY_METADATA_PATCHED_SHA256
        ):
            raise RuntimeError("Internal infinity-sdk METADATA patch verification failed")
        atomic_write(metadata_path, patched)

    if record_status == "original":
        atomic_write(record_path, patched_record_bytes(record_lines, metadata_index))

    final_metadata = metadata_path.read_bytes()
    final_record = record_path.read_bytes()
    if metadata_state(final_metadata) != "patched":
        raise RuntimeError("infinity-sdk METADATA did not reach the patched state")
    final_record_status, _, _ = inspect_record(
        final_record, record_path, metadata_entry
    )
    if final_record_status != "patched":
        raise RuntimeError("infinity-sdk RECORD did not reach the patched state")
    print(
        "[OK  ] Repaired infinity-sdk 0.7.3 NumPy requirement and wheel RECORD"
    )
    return metadata_path, record_path


def locate_dist_info(distribution_name: str, version: str, dirname: str) -> Path:
    try:
        distribution = importlib.metadata.distribution(distribution_name)
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError(
            f"{distribution_name} {version} is not installed in {sys.executable}"
        ) from exc
    if distribution.version != version:
        raise RuntimeError(
            f"Unsupported {distribution_name} version {distribution.version!r}; "
            f"expected {version!r}"
        )
    files = distribution.files
    if files is None:
        raise RuntimeError(f"{distribution_name} has no installed RECORD")
    metadata_entries = [
        item
        for item in files
        if item.name == "METADATA" and item.parent.name.endswith(".dist-info")
    ]
    if len(metadata_entries) != 1:
        raise RuntimeError(
            f"Could not uniquely locate {distribution_name} dist-info; found "
            f"{len(metadata_entries)} METADATA entries"
        )
    metadata_path = Path(distribution.locate_file(metadata_entries[0]))
    if metadata_path.parent.name != dirname:
        raise RuntimeError(
            f"Unexpected {distribution_name} dist-info directory "
            f"{metadata_path.parent.name!r}; expected {dirname!r}"
        )
    return metadata_path.parent


def removed_requirement_metadata_state(
    patch: RemovedRequirementPatch, data: bytes
) -> str:
    size = len(data)
    digest = sha256_bytes(data)
    if size == patch.original_size and digest == patch.original_sha256:
        if data.count(patch.requirement) != 1:
            raise RuntimeError(
                f"Audited {patch.distribution} METADATA does not contain exactly "
                f"one {patch.requirement.rstrip()!r} line"
            )
        return "original"
    if size == patch.patched_size and digest == patch.patched_sha256:
        if patch.requirement in data:
            raise RuntimeError(
                f"Patched {patch.distribution} METADATA retains the excluded "
                "requirement"
            )
        return "patched"
    raise RuntimeError(
        f"Refusing to patch unknown {patch.distribution} METADATA: expected "
        f"SHA256 {patch.original_sha256} (original) or {patch.patched_sha256} "
        f"(patched), found {digest} ({size} bytes)"
    )


def inspect_removed_requirement_record(
    patch: RemovedRequirementPatch, data: bytes, record_path: Path
) -> tuple[str, list[bytes], int]:
    lines = data.splitlines(keepends=True)
    metadata_indices: list[int] = []
    self_rows = 0
    state: str | None = None
    original = [
        patch.metadata_entry,
        f"sha256={record_digest(patch.original_sha256)}",
        str(patch.original_size),
    ]
    patched = [
        patch.metadata_entry,
        f"sha256={record_digest(patch.patched_sha256)}",
        str(patch.patched_size),
    ]

    for index, raw_line in enumerate(lines):
        row = parse_record_row(raw_line, record_path)
        if row[0] == patch.metadata_entry:
            metadata_indices.append(index)
            if row == original:
                state = "original"
            elif row == patched:
                state = "patched"
            else:
                raise RuntimeError(
                    f"Unexpected {patch.distribution} METADATA row in "
                    f"{record_path}: {row!r}"
                )
        if row[0] == patch.record_entry:
            self_rows += 1
            if row[1:] != ["", ""]:
                raise RuntimeError(
                    f"Malformed {patch.distribution} RECORD self row in "
                    f"{record_path}: {row!r}"
                )

    if len(metadata_indices) != 1 or state is None:
        raise RuntimeError(
            f"Expected exactly one {patch.distribution} METADATA row in "
            f"{record_path}; found {len(metadata_indices)}"
        )
    if self_rows != 1:
        raise RuntimeError(
            f"Expected exactly one {patch.distribution} RECORD self row in "
            f"{record_path}; found {self_rows}"
        )
    return state, lines, metadata_indices[0]


def patch_removed_requirement_record(
    patch: RemovedRequirementPatch, lines: list[bytes], metadata_index: int
) -> bytes:
    current = lines[metadata_index]
    if current.endswith(b"\r\n"):
        ending = b"\r\n"
    elif current.endswith(b"\n"):
        ending = b"\n"
    elif current.endswith(b"\r"):
        ending = b"\r"
    else:
        ending = b""
    replacement = (
        f"{patch.metadata_entry},"
        f"sha256={record_digest(patch.patched_sha256)},"
        f"{patch.patched_size}"
    ).encode("ascii")
    lines[metadata_index] = replacement + ending
    return b"".join(lines)


def repair_removed_requirement_metadata(
    patch: RemovedRequirementPatch,
) -> tuple[Path, Path]:
    dist_info = locate_dist_info(
        patch.distribution, patch.version, patch.dist_info
    )
    metadata_path = dist_info / "METADATA"
    record_path = dist_info / "RECORD"
    for description, path in (
        ("METADATA", metadata_path),
        ("RECORD", record_path),
    ):
        if path.is_symlink():
            raise RuntimeError(
                f"{patch.distribution} {description} must not be a symlink: {path}"
            )
        if not path.is_file():
            raise RuntimeError(
                f"{patch.distribution} {description} is missing: {path}"
            )

    metadata = metadata_path.read_bytes()
    record = record_path.read_bytes()
    metadata_status = removed_requirement_metadata_state(patch, metadata)
    record_status, record_lines, metadata_index = (
        inspect_removed_requirement_record(patch, record, record_path)
    )

    # Complete either audited mixed state left by interruption. Unknown bytes or
    # RECORD rows have already failed without changing the installation.
    if metadata_status == "original":
        replacement = metadata.replace(patch.requirement, b"", 1)
        if (
            len(replacement) != patch.patched_size
            or sha256_bytes(replacement) != patch.patched_sha256
        ):
            raise RuntimeError(
                f"Internal {patch.distribution} METADATA patch verification failed"
            )
        atomic_write(metadata_path, replacement)
    if record_status == "original":
        atomic_write(
            record_path,
            patch_removed_requirement_record(patch, record_lines, metadata_index),
        )

    if removed_requirement_metadata_state(
        patch, metadata_path.read_bytes()
    ) != "patched":
        raise RuntimeError(
            f"{patch.distribution} METADATA did not reach the patched state"
        )
    final_record_status, _, _ = inspect_removed_requirement_record(
        patch, record_path.read_bytes(), record_path
    )
    if final_record_status != "patched":
        raise RuntimeError(
            f"{patch.distribution} RECORD did not reach the patched state"
        )
    print(
        f"[OK  ] Removed excluded {patch.requirement.rstrip().decode('ascii')} "
        f"from {patch.distribution} {patch.version} metadata"
    )
    return metadata_path, record_path


def repair_excluded_dependency_metadata() -> None:
    for patch in EXCLUDED_REQUIREMENT_PATCHES:
        repair_removed_requirement_metadata(patch)


def moodle_metadata_state(data: bytes) -> str:
    size = len(data)
    digest = sha256_bytes(data)
    if (
        size == MOODLE_METADATA_ORIGINAL_SIZE
        and digest == MOODLE_METADATA_ORIGINAL_SHA256
    ):
        if data.count(MOODLE_REQUIREMENT_ORIGINAL) != 1:
            raise RuntimeError("Audited moodlepy METADATA has an unexpected attrs line")
        return "original"
    if (
        size == MOODLE_METADATA_PATCHED_SIZE
        and digest == MOODLE_METADATA_PATCHED_SHA256
    ):
        if data.count(MOODLE_REQUIREMENT_PATCHED) != 1:
            raise RuntimeError("Patched moodlepy METADATA has an unexpected attrs line")
        if MOODLE_REQUIREMENT_ORIGINAL in data:
            raise RuntimeError("Patched moodlepy METADATA retains the stale attrs pin")
        return "patched"
    raise RuntimeError(
        "Refusing to patch unknown moodlepy METADATA: "
        f"expected SHA256 {MOODLE_METADATA_ORIGINAL_SHA256} (original) or "
        f"{MOODLE_METADATA_PATCHED_SHA256} (patched), found {digest} ({size} bytes)"
    )


def inspect_moodle_record(
    data: bytes, record_path: Path
) -> tuple[str, list[bytes], int]:
    lines = data.splitlines(keepends=True)
    metadata_indices: list[int] = []
    self_rows = 0
    metadata_entry = f"{MOODLE_DIST_INFO}/METADATA"
    record_entry = f"{MOODLE_DIST_INFO}/RECORD"
    state: str | None = None
    original = [
        metadata_entry,
        f"sha256={record_digest(MOODLE_METADATA_ORIGINAL_SHA256)}",
        str(MOODLE_METADATA_ORIGINAL_SIZE),
    ]
    patched = [
        metadata_entry,
        f"sha256={record_digest(MOODLE_METADATA_PATCHED_SHA256)}",
        str(MOODLE_METADATA_PATCHED_SIZE),
    ]

    for index, raw_line in enumerate(lines):
        row = parse_record_row(raw_line, record_path)
        if row[0] == metadata_entry:
            metadata_indices.append(index)
            if row == original:
                state = "original"
            elif row == patched:
                state = "patched"
            else:
                raise RuntimeError(
                    f"Unexpected moodlepy METADATA row in {record_path}: {row!r}"
                )
        if row[0] == record_entry:
            self_rows += 1
            if row[1:] != ["", ""]:
                raise RuntimeError(
                    f"Malformed moodlepy RECORD self row in {record_path}: {row!r}"
                )

    if len(metadata_indices) != 1 or state is None:
        raise RuntimeError(
            f"Expected exactly one moodlepy METADATA row in {record_path}; "
            f"found {len(metadata_indices)}"
        )
    if self_rows != 1:
        raise RuntimeError(
            f"Expected exactly one moodlepy RECORD self row in {record_path}; "
            f"found {self_rows}"
        )
    return state, lines, metadata_indices[0]


def patched_moodle_record_bytes(lines: list[bytes], metadata_index: int) -> bytes:
    current = lines[metadata_index]
    if current.endswith(b"\r\n"):
        ending = b"\r\n"
    elif current.endswith(b"\n"):
        ending = b"\n"
    elif current.endswith(b"\r"):
        ending = b"\r"
    else:
        ending = b""
    entry = (
        f"{MOODLE_DIST_INFO}/METADATA,"
        f"sha256={record_digest(MOODLE_METADATA_PATCHED_SHA256)},"
        f"{MOODLE_METADATA_PATCHED_SIZE}"
    ).encode("ascii")
    lines[metadata_index] = entry + ending
    return b"".join(lines)


def patch_moodle_metadata() -> tuple[Path, Path]:
    dist_info = locate_dist_info(MOODLE_DISTRIBUTION, MOODLE_VERSION, MOODLE_DIST_INFO)
    metadata_path = dist_info / "METADATA"
    record_path = dist_info / "RECORD"
    for description, path in (
        ("METADATA", metadata_path),
        ("RECORD", record_path),
    ):
        if path.is_symlink():
            raise RuntimeError(f"moodlepy {description} must not be a symlink: {path}")
        if not path.is_file():
            raise RuntimeError(f"moodlepy {description} is missing: {path}")

    metadata = metadata_path.read_bytes()
    record = record_path.read_bytes()
    metadata_status = moodle_metadata_state(metadata)
    record_status, record_lines, metadata_index = inspect_moodle_record(
        record, record_path
    )

    if metadata_status == "original":
        patched = metadata.replace(
            MOODLE_REQUIREMENT_ORIGINAL,
            MOODLE_REQUIREMENT_PATCHED,
            1,
        )
        if (
            len(patched) != MOODLE_METADATA_PATCHED_SIZE
            or sha256_bytes(patched) != MOODLE_METADATA_PATCHED_SHA256
        ):
            raise RuntimeError("Internal moodlepy METADATA patch verification failed")
        atomic_write(metadata_path, patched)
    if record_status == "original":
        atomic_write(
            record_path,
            patched_moodle_record_bytes(record_lines, metadata_index),
        )

    if moodle_metadata_state(metadata_path.read_bytes()) != "patched":
        raise RuntimeError("moodlepy METADATA did not reach the patched state")
    final_record_status, _, _ = inspect_moodle_record(
        record_path.read_bytes(), record_path
    )
    if final_record_status != "patched":
        raise RuntimeError("moodlepy RECORD did not reach the patched state")
    print("[OK  ] Repaired moodlepy 0.24.1 attrs requirement and wheel RECORD")
    return metadata_path, record_path


def validate_datrie_direct_url(data: bytes, path: Path) -> None:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot parse {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Unexpected PEP 610 data in {path}: expected an object")

    url = value.get("url")
    parsed_url = urllib.parse.urlparse(url) if isinstance(url, str) else None
    if parsed_url is None or parsed_url.scheme.lower() != "file":
        raise RuntimeError(
            f"Refusing to remove an unexpected non-local datrie direct URL: {url!r}"
        )
    wheel_path = Path(urllib.request.url2pathname(parsed_url.path))
    basename = wheel_path.name
    if basename.lower() != DATRIE_WHEEL_NAME.lower():
        raise RuntimeError(
            f"Unexpected datrie wheel in {path}: {basename!r}; expected "
            f"{DATRIE_WHEEL_NAME!r}"
        )

    archive_info = value.get("archive_info")
    if not isinstance(archive_info, dict):
        raise RuntimeError(f"Missing archive_info in {path}")
    advertised: list[str] = []
    direct_hash = archive_info.get("hash")
    if isinstance(direct_hash, str):
        digest = datrie_sha256_from_direct_url_hash(direct_hash)
        if digest is not None:
            advertised.append(digest)
    hashes = archive_info.get("hashes")
    if isinstance(hashes, dict) and isinstance(hashes.get("sha256"), str):
        advertised.append(hashes["sha256"])
    if advertised:
        if any(item != DATRIE_WHEEL_SHA256 for item in advertised):
            raise RuntimeError(
                f"Unexpected datrie wheel hash in {path}: {advertised!r}"
            )
        return
    validate_datrie_wheel_file(wheel_path, path)


def validate_datrie_wheel_file(wheel_path: Path, direct_url_path: Path) -> None:
    if wheel_path.is_symlink():
        raise RuntimeError(
            f"Refusing to verify datrie wheel through a symlink from {direct_url_path}: "
            f"{wheel_path}"
        )
    if not wheel_path.is_file():
        raise RuntimeError(
            f"Missing datrie wheel hash in {direct_url_path} and local wheel is not "
            f"available for verification: {wheel_path}"
        )
    digest = sha256_file(wheel_path)
    if digest != DATRIE_WHEEL_SHA256:
        raise RuntimeError(
            f"Missing datrie wheel hash in {direct_url_path} and local wheel hash "
            f"is unexpected: {digest}"
        )


def datrie_sha256_from_direct_url_hash(value: str) -> str | None:
    for prefix in DATRIE_HASH_PREFIXES:
        if value.startswith(prefix):
            return value.removeprefix(prefix)
    return None


def remove_datrie_direct_url() -> tuple[Path, Path]:
    """Remove PEP 610's absolute local-wheel path and its RECORD entry."""

    dist_info = locate_dist_info(DATRIE_DISTRIBUTION, DATRIE_VERSION, DATRIE_DIST_INFO)
    direct_url_path = dist_info / "direct_url.json"
    record_path = dist_info / "RECORD"
    if not record_path.is_file():
        raise RuntimeError(f"datrie RECORD is missing: {record_path}")
    if direct_url_path.is_symlink():
        raise RuntimeError(f"datrie direct_url.json must not be a symlink: {direct_url_path}")

    direct_data: bytes | None = None
    if direct_url_path.exists():
        if not direct_url_path.is_file():
            raise RuntimeError(f"datrie direct URL is not a file: {direct_url_path}")
        direct_data = direct_url_path.read_bytes()
        validate_datrie_direct_url(direct_data, direct_url_path)

    record_data = record_path.read_bytes()
    lines = record_data.splitlines(keepends=True)
    direct_entry = f"{DATRIE_DIST_INFO}/direct_url.json"
    direct_indices: list[int] = []
    for index, raw_line in enumerate(lines):
        row = parse_record_row(raw_line, record_path)
        if row[0] == direct_entry:
            direct_indices.append(index)
            if direct_data is not None:
                expected = [
                    direct_entry,
                    f"sha256={record_digest(sha256_bytes(direct_data))}",
                    str(len(direct_data)),
                ]
                if row != expected:
                    raise RuntimeError(
                        f"datrie direct_url.json RECORD row is inconsistent: {row!r}"
                    )
    if len(direct_indices) > 1:
        raise RuntimeError(
            f"Duplicate datrie direct_url.json rows in {record_path}: "
            f"{len(direct_indices)}"
        )

    if direct_indices:
        sanitized_record = b"".join(
            line for index, line in enumerate(lines) if index != direct_indices[0]
        )
        atomic_write(record_path, sanitized_record)
    if direct_data is not None:
        direct_url_path.unlink()

    if direct_url_path.exists() or direct_url_path.is_symlink():
        raise RuntimeError(f"Could not remove build-bound path: {direct_url_path}")
    for raw_line in record_path.read_bytes().splitlines(keepends=True):
        if parse_record_row(raw_line, record_path)[0] == direct_entry:
            raise RuntimeError(f"Build-bound datrie RECORD row remains in {record_path}")
    print("[OK  ] Removed datrie local-wheel direct_url.json provenance")
    return direct_url_path, record_path


def expected_record() -> dict[str, object]:
    return {
        "schema_version": 5,
        "helper": "prepare_ragflow_windows.py",
        "ragflow": {
            "version": RAGFLOW_VERSION,
            "target": TASK_HANDLER_RELATIVE.as_posix(),
            "original_sha256": TASK_HANDLER_ORIGINAL_SHA256,
            "patched_sha256": TASK_HANDLER_PATCHED_SHA256,
            "change": "lazy GraphRAG import for normal non-GraphRAG workers",
        },
        "graphrag_native_adapter": {
            "leiden_target": LEIDEN_RELATIVE.as_posix(),
            "leiden_original_sha256": LEIDEN_ORIGINAL_SHA256,
            "leiden_patched_sha256": LEIDEN_PATCHED_SHA256,
            "adapter_target": LEIDEN_ADAPTER_RELATIVE.as_posix(),
            "adapter_sha256": LEIDEN_ADAPTER_SHA256,
            "distribution": "graspologic-native==1.2.5",
            "change": (
                "hierarchical Leiden uses the upstream Rust backend; largest "
                "connected component uses NetworkX"
            ),
        },
        "infinity_sdk": {
            "version": INFINITY_VERSION,
            "metadata_path": f"{INFINITY_DIST_INFO}/METADATA",
            "metadata_original_sha256": INFINITY_METADATA_ORIGINAL_SHA256,
            "metadata_patched_sha256": INFINITY_METADATA_PATCHED_SHA256,
            "numpy_requirement": INFINITY_REQUIREMENT_PATCHED.decode("ascii").rstrip(),
            "record_entry": (
                f"{INFINITY_DIST_INFO}/METADATA,"
                f"sha256={INFINITY_RECORD_DIGEST_PATCHED},"
                f"{INFINITY_METADATA_PATCHED_SIZE}"
            ),
        },
        "datrie": {
            "version": DATRIE_VERSION,
            "wheel": DATRIE_WHEEL_NAME,
            "wheel_sha256": DATRIE_WHEEL_SHA256,
            "removed": f"{DATRIE_DIST_INFO}/direct_url.json",
            "reason": "remove the absolute online-build wheel path",
        },
        "moodlepy": {
            "version": MOODLE_VERSION,
            "metadata_path": f"{MOODLE_DIST_INFO}/METADATA",
            "metadata_original_sha256": MOODLE_METADATA_ORIGINAL_SHA256,
            "metadata_patched_sha256": MOODLE_METADATA_PATCHED_SHA256,
            "attrs_requirement": MOODLE_REQUIREMENT_PATCHED.decode("ascii").rstrip(),
            "record_entry": (
                f"{MOODLE_DIST_INFO}/METADATA,"
                f"sha256={record_digest(MOODLE_METADATA_PATCHED_SHA256)},"
                f"{MOODLE_METADATA_PATCHED_SIZE}"
            ),
            "reason": (
                "RAGFlow pins trio>=0.26 for CPython 3.13 and overrides attrs "
                "to >=23.2.0; moodlepy 0.24.1 metadata still advertises "
                "the stale attrs<23 constraint"
            ),
        },
        "excluded_dependency_metadata": {
            patch.distribution: {
                "version": patch.version,
                "metadata_path": patch.metadata_entry,
                "metadata_original_sha256": patch.original_sha256,
                "metadata_patched_sha256": patch.patched_sha256,
                "removed_requirement": patch.requirement.decode("ascii").rstrip(),
                "record_entry": (
                    f"{patch.metadata_entry},"
                    f"sha256={record_digest(patch.patched_sha256)},"
                    f"{patch.patched_size}"
                ),
                "reason": "dependency is intentionally excluded from this profile",
            }
            for patch in EXCLUDED_REQUIREMENT_PATCHES
        },
    }


def write_or_verify_record(path: Path) -> None:
    expected = json.dumps(
        expected_record(), ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    if path.is_symlink():
        raise RuntimeError(f"Compatibility record must not be a symlink: {path}")
    if path.exists():
        if not path.is_file():
            raise RuntimeError(f"Compatibility record is not a file: {path}")
        actual = path.read_bytes()
        if actual != expected:
            raise RuntimeError(
                f"Existing compatibility record is stale or modified: {path}. "
                "Remove it only after reviewing the changed provenance."
            )
        print(f"[OK  ] Compatibility record verified: {path}")
        return
    atomic_write(path, expected)
    if path.read_bytes() != expected:
        raise RuntimeError(f"Compatibility record verification failed: {path}")
    print(f"[OK  ] Compatibility record written: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply audited RAGFlow 0.27.1 native-Windows compatibility fixes"
    )
    parser.add_argument("--ragflow-dir", required=True, type=Path)
    parser.add_argument("--record", required=True, type=Path)
    args = parser.parse_args()

    ragflow_dir = args.ragflow_dir.resolve()
    print(f"[STEP] Verify RAGFlow {RAGFLOW_VERSION} source")
    ragflow_source_version(ragflow_dir)
    print("[STEP] Keep GraphRAG lazy for non-GraphRAG workers")
    patch_task_handler(ragflow_dir)
    print("[STEP] Enable GraphRAG through the audited graspologic-native adapter")
    install_graphrag_native_adapter(ragflow_dir)
    print("[STEP] Repair infinity-sdk metadata for the pinned NumPy 2 runtime")
    patch_infinity_metadata()
    print("[STEP] Repair moodlepy metadata for the pinned attrs runtime")
    patch_moodle_metadata()
    print("[STEP] Repair metadata for intentionally excluded dependencies")
    repair_excluded_dependency_metadata()
    print("[STEP] Remove build-bound datrie wheel provenance")
    remove_datrie_direct_url()
    print("[STEP] Write deterministic compatibility provenance")
    write_or_verify_record(args.record.resolve())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
