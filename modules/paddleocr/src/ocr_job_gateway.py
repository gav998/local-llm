#!/usr/bin/env python3
"""Local async PaddleOCR job API compatible with RAGFlow 0.27.1.

The stock Python RAGFlow parser calls ``/api/v2/ocr/jobs``. PaddleX basic
serving exposes a different synchronous endpoint, so this small loopback-only
gateway owns one strict-GPU PP-StructureV3 pipeline and adapts the protocol.
``LOCAL_OCR_DEVICE`` selects ``cpu`` or a CUDA board. Use ``auto`` to prefer
GPU 1 when two boards are visible, keeping GPU 0 free for embeddings by default.
"""

from __future__ import annotations

import argparse
import copy
import html
from html.parser import HTMLParser
import hmac
import json
import os
import queue
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

MODEL_NAMES = (
    "PP-DocLayout-L",
    "PP-DocBlockLayout",
    "PP-OCRv6_medium_det",
    "eslav_PP-OCRv5_mobile_rec",
    "SLANet_plus",
)
MAX_UPLOAD_BYTES = int(os.environ.get("LOCAL_OCR_MAX_UPLOAD_BYTES", str(512 << 20)))
DEFAULT_TEXT_RECOGNITION_BATCH_SIZE = 8
DEFAULT_MAX_BLOCK_TOKENS = 900
ATOMIC_REPLACE_TIMEOUT_SECONDS = 2.0
ATOMIC_REPLACE_RETRY_SECONDS = 0.02
APPROX_TOKEN_PATTERN = re.compile(r"<[^>]+>|[\w]+|[^\w\s]", re.UNICODE)
HTML_TABLE_PATTERN = re.compile(r"<\s*/?\s*(table|thead|tbody|tr|td|th)\b", re.I)
HTML_ROW_BOUNDARY_PATTERN = re.compile(r"(<\s*/\s*tr\s*>)\s*(<\s*tr\b)", re.I)

PREDICT_OPTION_MAP = {
    "useDocOrientationClassify": "use_doc_orientation_classify",
    "useDocUnwarping": "use_doc_unwarping",
    "useTextlineOrientation": "use_textline_orientation",
    "useSealRecognition": "use_seal_recognition",
    "useTableRecognition": "use_table_recognition",
    "useFormulaRecognition": "use_formula_recognition",
    "useChartRecognition": "use_chart_recognition",
    "useRegionDetection": "use_region_detection",
    "formatBlockContent": "format_block_content",
    "layoutThreshold": "layout_threshold",
    "layoutNms": "layout_nms",
    "layoutUnclipRatio": "layout_unclip_ratio",
    "layoutMergeBboxesMode": "layout_merge_bboxes_mode",
    "textDetLimitSideLen": "text_det_limit_side_len",
    "textDetLimitType": "text_det_limit_type",
    "textDetThresh": "text_det_thresh",
    "textDetBoxThresh": "text_det_box_thresh",
    "textDetUnclipRatio": "text_det_unclip_ratio",
    "textRecScoreThresh": "text_rec_score_thresh",
    "markdownIgnoreLabels": "markdown_ignore_labels",
}
DISABLED_FEATURES = {
    "useDocOrientationClassify",
    "useDocUnwarping",
    "useTextlineOrientation",
    "useSealRecognition",
    "useFormulaRecognition",
    "useChartRecognition",
}
REQUIRED_FEATURES = {"useTableRecognition"}
IGNORED_CLOUD_OPTIONS = {
    "prettifyMarkdown",
    "showFormulaNumber",
    "visualize",
    "useLayoutDetection",
    "useOcrForImageBlock",
    "layoutShapeMode",
    "promptLabel",
    "repetitionPenalty",
    "temperature",
    "topP",
    "minPixels",
    "maxPixels",
    "maxNewTokens",
    "mergeLayoutBlocks",
    "restructurePages",
    "mergeTables",
    "relevelTitles",
    "vlmExtraArgs",
}


class QueueBusyError(RuntimeError):
    pass


class HtmlTableExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, str]]] = []
        self._current_row: list[tuple[str, str]] | None = None
        self._current_cell_tag: str | None = None
        self._current_cell_text: list[str] = []
        self._table_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._table_depth += 1
            return
        if self._table_depth < 1:
            return
        if tag == "tr":
            self._finish_cell()
            self._current_row = []
            return
        if tag in {"td", "th"} and self._current_row is not None:
            self._finish_cell()
            self._current_cell_tag = tag
            self._current_cell_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"}:
            self._finish_cell()
            return
        if tag == "tr":
            self._finish_cell()
            if self._current_row:
                self.rows.append(self._current_row)
            self._current_row = None
            return
        if tag == "table" and self._table_depth:
            self._table_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._current_cell_tag is not None:
            self._current_cell_text.append(data)

    def _finish_cell(self) -> None:
        if self._current_cell_tag is None or self._current_row is None:
            return
        text = " ".join("".join(self._current_cell_text).split())
        self._current_row.append((self._current_cell_tag, text))
        self._current_cell_tag = None
        self._current_cell_text = []


def approximate_token_count(text: str) -> int:
    count = 0
    for piece in APPROX_TOKEN_PATTERN.findall(text):
        if piece.startswith("<"):
            count += max(1, (len(piece) + 11) // 12)
        elif re.match(r"^\w+$", piece, re.UNICODE):
            count += max(1, (len(piece) + 3) // 4)
        else:
            count += 1
    return count


def split_text_by_budget(text: str, max_tokens: int) -> list[str]:
    if approximate_token_count(text) <= max_tokens:
        return [text]

    parts = re.findall(r"\S+\s*", text, re.UNICODE)
    if not parts:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for part in parts:
        part_tokens = approximate_token_count(part)
        if part_tokens > max_tokens:
            if current:
                chunks.append("".join(current).strip())
                current = []
                current_tokens = 0
            chunks.extend(split_oversized_word(part.strip(), max_tokens))
            continue
        if current and current_tokens + part_tokens > max_tokens:
            chunks.append("".join(current).strip())
            current = []
            current_tokens = 0
        current.append(part)
        current_tokens += part_tokens
    if current:
        chunks.append("".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def split_oversized_word(text: str, max_tokens: int) -> list[str]:
    max_chars = max(32, max_tokens * 3)
    return [text[index : index + max_chars] for index in range(0, len(text), max_chars)]


def row_to_html(row: list[tuple[str, str]]) -> str:
    cells = "".join(
        f"<{tag}>{html.escape(text, quote=False)}</{tag}>" for tag, text in row
    )
    return f"<tr>{cells}</tr>"


def table_html(rows: list[list[tuple[str, str]]]) -> str:
    return "<table>\n" + "\n".join(row_to_html(row) for row in rows) + "\n</table>"


def extract_table_rows(content: str) -> list[list[tuple[str, str]]]:
    parser = HtmlTableExtractor()
    try:
        parser.feed(content)
        parser.close()
    except Exception:
        return []
    return [row for row in parser.rows if row]


def split_table_html(content: str, max_tokens: int) -> list[str]:
    rows = extract_table_rows(content)
    if len(rows) < 2:
        normalized = HTML_ROW_BOUNDARY_PATTERN.sub(r"\1\n\2", content)
        return split_text_by_budget(normalized, max_tokens)

    header = rows[0] if all(tag == "th" for tag, _ in rows[0]) else None
    body_rows = rows[1:] if header is not None else rows
    chunks: list[str] = []
    current_rows: list[list[tuple[str, str]]] = []

    def candidate(extra_rows: list[list[tuple[str, str]]]) -> list[list[tuple[str, str]]]:
        if header is not None:
            return [header, *current_rows, *extra_rows]
        return [*current_rows, *extra_rows]

    def flush() -> None:
        nonlocal current_rows
        if current_rows:
            chunks.append(table_html(candidate([])))
            current_rows = []

    for row in body_rows:
        row_rows = [row]
        row_text = table_html(([header] if header is not None else []) + row_rows)
        if approximate_token_count(row_text) > max_tokens:
            flush()
            chunks.extend(split_oversized_table_row(header, row, max_tokens))
            continue
        next_text = table_html(candidate(row_rows))
        if current_rows and approximate_token_count(next_text) > max_tokens:
            flush()
        current_rows.append(row)
    flush()
    if not chunks and header is not None:
        chunks.append(table_html([header]))
    return chunks


def split_oversized_table_row(
    header: list[tuple[str, str]] | None,
    row: list[tuple[str, str]],
    max_tokens: int,
) -> list[str]:
    chunks: list[str] = []
    for cell_index, (_tag, text) in enumerate(row, start=1):
        field_name = f"Колонка {cell_index}"
        if header is not None and cell_index <= len(header):
            field_name = header[cell_index - 1][1] or field_name
        field_header = [("th", "Поле"), ("th", "Значение")]
        empty_field = table_html([field_header, [("td", field_name), ("td", "")]])
        part_budget = max(16, max_tokens - approximate_token_count(empty_field))
        for part in split_text_by_budget(text, part_budget):
            field_table = table_html(
                [field_header, [("td", field_name), ("td", part)]]
            )
            if approximate_token_count(field_table) <= max_tokens:
                chunks.append(field_table)
            else:
                chunks.extend(split_text_by_budget(f"{field_name}: {part}", max_tokens))
    return chunks


def split_block_content(content: str, label: str, max_tokens: int) -> list[str]:
    if approximate_token_count(content) <= max_tokens:
        return [content]
    if label == "table" or HTML_TABLE_PATTERN.search(content):
        return split_table_html(content, max_tokens)
    return split_text_by_budget(content, max_tokens)


def bound_parsing_blocks(blocks: list[Any], max_tokens: int) -> list[Any]:
    bounded: list[Any] = []
    for block in blocks:
        if not isinstance(block, dict):
            bounded.append(block)
            continue
        content = block.get("block_content")
        if not isinstance(content, str) or not content.strip():
            bounded.append(block)
            continue
        label = str(block.get("block_label") or "")
        parts = split_block_content(content, label, max_tokens)
        if len(parts) == 1 and parts[0] == content:
            bounded.append(block)
            continue
        for part in parts:
            split_block = copy.deepcopy(block)
            split_block["block_content"] = part
            bounded.append(split_block)
    return bounded


def bound_result_blocks(value: Any, max_tokens: int) -> Any:
    if isinstance(value, dict):
        bounded = {}
        for key, item in value.items():
            if key == "parsing_res_list" and isinstance(item, list):
                bounded[key] = bound_parsing_blocks(item, max_tokens)
            else:
                bounded[key] = bound_result_blocks(item, max_tokens)
        return bounded
    if isinstance(value, (list, tuple)):
        return [bound_result_blocks(item, max_tokens) for item in value]
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # Windows denies replacement while another thread has the destination open
    # for reading. Status polling normally releases it immediately, so tolerate
    # that transient sharing violation while retaining atomic state updates.
    deadline = time.monotonic() + ATOMIC_REPLACE_TIMEOUT_SECONDS
    while True:
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(ATOMIC_REPLACE_RETRY_SECONDS)


def read_json(path: Path) -> dict[str, Any]:
    # A concurrent atomic replacement can also make the destination briefly
    # unavailable to readers on Windows. Retry the open instead of turning a
    # normal state transition into a transient HTTP 500 response.
    deadline = time.monotonic() + ATOMIC_REPLACE_TIMEOUT_SECONDS
    while True:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(ATOMIC_REPLACE_RETRY_SECONDS)


def prune_result(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: prune_result(item)
            for key, item in value.items()
            if key not in {"input_path", "page_index"}
        }
    if isinstance(value, (list, tuple)):
        return [prune_result(item) for item in value]
    return value


def detect_suffix(header: bytes) -> str:
    if header.startswith(b"%PDF-"):
        return ".pdf"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if header.startswith((b"II*\x00", b"MM\x00*")):
        return ".tif"
    if header.startswith(b"BM"):
        return ".bmp"
    raise ValueError("Only PDF, PNG, JPEG, TIFF and BMP inputs are accepted")


def build_predict_options(optional_payload: dict[str, Any]) -> dict[str, Any]:
    unknown = set(optional_payload) - set(PREDICT_OPTION_MAP) - IGNORED_CLOUD_OPTIONS
    if unknown:
        raise ValueError(f"Unsupported optionalPayload fields: {sorted(unknown)}")
    enabled_but_unavailable = sorted(
        key for key in DISABLED_FEATURES if optional_payload.get(key) is True
    )
    if enabled_but_unavailable:
        raise ValueError(
            "Features disabled by the 8 GiB profile were requested: "
            + ", ".join(enabled_but_unavailable)
        )
    disabled_but_required = sorted(
        key
        for key in REQUIRED_FEATURES
        if key in optional_payload and optional_payload[key] is not True
    )
    if disabled_but_required:
        raise ValueError(
            "Features required by the table-aware profile cannot be disabled: "
            + ", ".join(disabled_but_required)
        )

    options: dict[str, Any] = {
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "use_seal_recognition": False,
        "use_table_recognition": True,
        "use_formula_recognition": False,
        "use_chart_recognition": False,
        "use_region_detection": True,
        "format_block_content": True,
        # Reuse the page-wide East Slavic OCR boxes/text. The table model adds
        # structure without instantiating a second OCR pipeline per table.
        "use_ocr_results_with_table_cells": False,
    }
    for api_name, python_name in PREDICT_OPTION_MAP.items():
        value = optional_payload.get(api_name)
        if value is not None and api_name not in DISABLED_FEATURES:
            options[python_name] = value
    return options


def validate_pipeline_profile(config: dict[str, Any]) -> None:
    """Reject profile values that make PaddleX load disabled GPU models."""
    disabled_doc_preprocessor_options = (
        "use_doc_preprocessor",
        "use_doc_orientation_classify",
        "use_doc_unwarping",
    )
    for option in disabled_doc_preprocessor_options:
        if config.get(option) is not False:
            raise RuntimeError(
                f"The 8 GiB profile must explicitly set {option}: false"
            )
    if config.get("use_table_recognition") is not True:
        raise RuntimeError("PP-StructureV3 table recognition must be enabled")
    table_config = config.get("SubPipelines", {}).get("TableRecognition", {})
    if table_config.get("pipeline_name") != "table_recognition":
        raise RuntimeError("The audited compact table_recognition pipeline is missing")
    if table_config.get("use_ocr_model") is not False:
        raise RuntimeError("Table recognition must reuse the page-wide OCR result")


def parse_positive_int(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer, got {value!r}") from exc
    if parsed < 1:
        raise RuntimeError(f"{name} must be a positive integer, got {value!r}")
    return parsed


def parse_non_negative_int(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a non-negative integer, got {value!r}") from exc
    if parsed < 0:
        raise RuntimeError(f"{name} must be a non-negative integer, got {value!r}")
    return parsed


def resolve_device(device_count: int) -> tuple[str, int | None, str, str]:
    requested = (
        os.environ.get(
            "LOCAL_OCR_DEVICE",
            os.environ.get(
                "LOCAL_OCR_REQUESTED_DEVICE",
                os.environ.get("LOCAL_OCR_GPU_INDEX", "auto"),
            ),
        )
        .strip()
        .lower()
    )
    if requested == "":
        requested = "auto"
    if requested == "cpu":
        return "cpu", None, requested, "cpu-explicit"
    if requested == "auto":
        prefer_text = os.environ.get("LOCAL_OCR_PREFER_GPU_INDEX", "1").strip()
        preferred = parse_non_negative_int(prefer_text, "LOCAL_OCR_PREFER_GPU_INDEX")
        if preferred < device_count:
            return f"gpu:{preferred}", preferred, requested, f"auto-prefer-{preferred}"
        return "gpu:0", 0, requested, "auto-fallback-0"
    if requested.startswith("gpu:"):
        gpu_index_text = requested.removeprefix("gpu:")
        gpu_index = parse_non_negative_int(gpu_index_text, "LOCAL_OCR_DEVICE")
        return f"gpu:{gpu_index}", gpu_index, requested, "explicit"

    try:
        gpu_index = int(requested)
    except ValueError as exc:
        raise RuntimeError(
            "LOCAL_OCR_DEVICE must be 'cpu', 'auto' or a non-negative integer, "
            f"got {requested!r}"
        ) from exc
    return f"gpu:{gpu_index}", gpu_index, requested, "explicit"


def list_cuda_devices() -> int:
    import paddle

    report: dict[str, Any] = {
        "paddle_version": paddle.__version__,
        "compiled_with_cuda": bool(paddle.is_compiled_with_cuda()),
        "paddle_cuda_version": str(paddle.version.cuda),
        "devices": [],
    }
    if paddle.is_compiled_with_cuda():
        count = paddle.device.cuda.device_count()
        report["cuda_device_count"] = count
        for index in range(count):
            try:
                capability = paddle.device.cuda.get_device_capability(index)
            except Exception as exc:  # keep diagnostics best-effort
                capability = f"error: {exc}"
            report["devices"].append({"index": index, "capability": capability})
    else:
        report["cuda_device_count"] = 0
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def load_pipeline(config_path: Path, model_root: Path):
    if os.environ.get("PADDLE_PDX_DISABLE_DEVICE_FALLBACK") != "1":
        raise RuntimeError("PADDLE_PDX_DISABLE_DEVICE_FALLBACK must be 1")
    for name in MODEL_NAMES:
        for filename in ("inference.json", "inference.yml", "inference.pdiparams"):
            key = model_root / name / filename
            if key.is_symlink() or not key.is_file():
                raise RuntimeError(f"Prepared model is incomplete: {key}")

    import paddle
    import yaml

    compiled_cuda = str(paddle.version.cuda)
    cuda_compiled = bool(paddle.is_compiled_with_cuda())
    count = paddle.device.cuda.device_count() if cuda_compiled else 0
    device, gpu_index, requested_device, device_selection = resolve_device(count)
    strict_gpu = device.startswith("gpu:")

    if strict_gpu and not cuda_compiled:
        raise RuntimeError("This PaddlePaddle build has no CUDA support")
    if strict_gpu and compiled_cuda != "11.8":
        raise RuntimeError(
            f"This runtime requires the pinned CUDA 11.8 wheel, got CUDA {compiled_cuda}"
        )
    if strict_gpu and count < 1:
        raise RuntimeError("No CUDA device is visible to PaddlePaddle")

    if strict_gpu and (gpu_index is None or gpu_index < 0 or gpu_index >= count):
        raise RuntimeError(
            f"LOCAL_OCR_GPU_INDEX={gpu_index} is outside the visible GPU range 0..{count - 1}"
        )
    if not strict_gpu and os.environ.get("LOCAL_OCR_ALLOW_CPU") != "1":
        raise RuntimeError(
            "OCR resolved to CPU, but CPU mode is allowed only for "
            "LOCAL-LLM.bat start ingestion-cpu"
        )

    paddle.set_device(device)
    capability: tuple[int, int] | None = None
    compiled_arches: tuple[int, ...] = ()
    probe_tensor_device = paddle.device.get_device()
    if strict_gpu:
        assert gpu_index is not None
        capability = tuple(paddle.device.cuda.get_device_capability(gpu_index))
        if capability < (6, 1):
            raise RuntimeError(
                f"GPU {gpu_index} compute capability {capability} is too old"
            )

        compiled_arches = tuple(int(arch) for arch in paddle.version.cuda_archs())
        wanted_arch = capability[0] * 10 + capability[1]
        if wanted_arch not in compiled_arches:
            raise RuntimeError(
                f"The wheel lacks code for sm_{wanted_arch}; compiled arches: {compiled_arches}"
            )

    left = paddle.randn([256, 256], dtype="float32")
    right = paddle.randn([256, 256], dtype="float32")
    probe_value = float(paddle.sum(paddle.matmul(left, right)).numpy().item())

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    validate_pipeline_profile(config)
    text_recognition_batch_size = parse_positive_int(
        os.environ.get(
            "LOCAL_OCR_TEXT_REC_BATCH_SIZE",
            str(DEFAULT_TEXT_RECOGNITION_BATCH_SIZE),
        ).strip(),
        "LOCAL_OCR_TEXT_REC_BATCH_SIZE",
    )
    table_config = config.get("SubPipelines", {}).get("TableRecognition", {})
    config["SubModules"]["LayoutDetection"]["model_dir"] = str(
        model_root / "PP-DocLayout-L"
    )
    config["SubModules"]["RegionDetection"]["model_dir"] = str(
        model_root / "PP-DocBlockLayout"
    )
    config["SubPipelines"]["GeneralOCR"]["SubModules"]["TextDetection"]["model_dir"] = str(
        model_root / "PP-OCRv6_medium_det"
    )
    config["SubPipelines"]["GeneralOCR"]["SubModules"]["TextRecognition"]["model_dir"] = str(
        model_root / "eslav_PP-OCRv5_mobile_rec"
    )
    config["SubPipelines"]["GeneralOCR"]["SubModules"]["TextRecognition"][
        "batch_size"
    ] = text_recognition_batch_size
    table_config["SubModules"]["TableStructureRecognition"]["model_dir"] = str(
        model_root / "SLANet_plus"
    )

    from paddleocr import PPStructureV3

    pipeline = PPStructureV3(paddlex_config=config, device=device)
    runtime_info = {
        "strict_gpu": strict_gpu,
        "device": device,
        "runtime_device": paddle.device.get_device(),
        "requested_device": requested_device,
        "device_selection": device_selection,
        "paddle_version": paddle.__version__,
        "paddle_cuda_version": compiled_cuda,
        "compiled_with_cuda": cuda_compiled,
        "cuda_device_count": count,
        "gpu_index": gpu_index,
        "gpu_device": device,
        "gpu_selection": device_selection,
        "gpu_capability": ".".join(str(part) for part in capability)
        if capability is not None
        else None,
        "compiled_cuda_arches": compiled_arches,
        "cuda_tensor_probe_value": probe_value,
        "probe_tensor_device": probe_tensor_device,
        "text_recognition_batch_size": text_recognition_batch_size,
        "table_recognition": True,
        "table_structure_model": "SLANet_plus",
    }
    return pipeline, runtime_info


def load_strict_gpu_pipeline(config_path: Path, model_root: Path):
    pipeline, runtime_info = load_pipeline(config_path, model_root)
    if not runtime_info["strict_gpu"]:
        raise RuntimeError("Strict GPU startup resolved to CPU")
    return pipeline, runtime_info


class JobManager:
    def __init__(
        self,
        pipeline: Any,
        jobs_root: Path,
        public_url: str,
        gpu_info: dict[str, Any],
        max_queued_jobs: int = 8,
        max_block_tokens: int = DEFAULT_MAX_BLOCK_TOKENS,
    ) -> None:
        self.pipeline = pipeline
        self.jobs_root = jobs_root
        self.public_url = public_url.rstrip("/")
        self.gpu_info = gpu_info
        self.max_block_tokens = max_block_tokens
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self._queue: queue.Queue[tuple[str, Path, dict[str, Any]] | None] = queue.Queue(
            maxsize=max_queued_jobs
        )
        self._closed = False
        self._recover_interrupted_jobs()
        gpu_index = gpu_info.get("gpu_index", 0)
        self._worker = threading.Thread(
            target=self._run, name=f"ocr-gpu-{gpu_index}", daemon=True
        )
        self._worker.start()

    def _state_path(self, job_id: str) -> Path:
        return self.jobs_root / job_id / "state.json"

    def _result_path(self, job_id: str) -> Path:
        return self.jobs_root / job_id / "result.jsonl"

    def _recover_interrupted_jobs(self) -> None:
        for state_path in self.jobs_root.glob("*/state.json"):
            try:
                state = read_json(state_path)
            except (OSError, ValueError):
                continue
            if state.get("state") in {"queued", "running"}:
                state.update(
                    state="failed",
                    errorMsg="Gateway was restarted before this job completed",
                    updated=time.time(),
                )
                atomic_json(state_path, state)

    def submit(
        self, job_id: str, input_path: Path, predict_options: dict[str, Any]
    ) -> None:
        state = {
            "jobId": job_id,
            "state": "queued",
            "created": time.time(),
            "updated": time.time(),
        }
        atomic_json(self._state_path(job_id), state)
        try:
            self._queue.put_nowait((job_id, input_path, predict_options))
        except queue.Full as exc:
            state.update(
                state="failed", errorMsg="The local OCR queue is full", updated=time.time()
            )
            atomic_json(self._state_path(job_id), state)
            raise QueueBusyError(state["errorMsg"]) from exc

    def status(self, job_id: str) -> dict[str, Any]:
        path = self._state_path(job_id)
        try:
            state = read_json(path)
        except FileNotFoundError as exc:
            raise KeyError(job_id) from exc
        if state.get("state") == "done":
            state["resultJsonUrl"] = (
                f"{self.public_url}/api/v2/ocr/jobs/{job_id}/result"
            )
        return state

    def result_path(self, job_id: str) -> Path:
        path = self._result_path(job_id)
        if not path.is_file():
            raise KeyError(job_id)
        return path

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            job_id, input_path, predict_options = item
            state_path = self._state_path(job_id)
            state = read_json(state_path)
            try:
                state.update(state="running", updated=time.time())
                atomic_json(state_path, state)
                results = list(self.pipeline.predict(str(input_path), **predict_options))
                if not results:
                    raise RuntimeError("PP-StructureV3 returned no page results")

                result_path = self._result_path(job_id)
                temporary = result_path.with_suffix(".jsonl.tmp")
                with temporary.open("w", encoding="utf-8", newline="\n") as output:
                    for result in results:
                        pruned = prune_result(result.json["res"])
                        pruned = bound_result_blocks(pruned, self.max_block_tokens)
                        line = {
                            "logId": job_id,
                            "errorCode": 0,
                            "errorMsg": "Success",
                            "result": {
                                "layoutParsingResults": [{"prunedResult": pruned}],
                                "ocrResults": [],
                            },
                        }
                        output.write(json.dumps(line, ensure_ascii=False) + "\n")
                temporary.replace(result_path)
                state.update(state="done", updated=time.time())
            except Exception as exc:  # worker failures must be visible to RAGFlow
                state.update(state="failed", errorMsg=str(exc), updated=time.time())
            finally:
                atomic_json(state_path, state)
                input_path.unlink(missing_ok=True)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._worker.join(timeout=30)
        close = getattr(self.pipeline, "close", None)
        if close is not None:
            close()


def create_app(manager: JobManager, token: str) -> FastAPI:
    from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
    from fastapi.responses import FileResponse

    globals()["UploadFile"] = UploadFile
    app = FastAPI(title="local_llm PaddleOCR compatibility gateway")

    def require_token(authorization: str | None) -> None:
        expected = f"Bearer {token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="Invalid bearer token")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ready", **manager.gpu_info}

    @app.post("/api/v2/ocr/jobs")
    async def submit_job(
        file: UploadFile = File(...),
        model: str = Form(...),
        optionalPayload: str = Form("{}"),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_token(authorization)
        if model != "PP-StructureV3":
            raise HTTPException(status_code=400, detail="Only PP-StructureV3 is enabled")
        try:
            payload = json.loads(optionalPayload or "{}")
            if not isinstance(payload, dict):
                raise ValueError("optionalPayload must be a JSON object")
            predict_options = build_predict_options(payload)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        job_id = uuid.uuid4().hex + uuid.uuid4().hex
        job_dir = manager.jobs_root / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        partial = job_dir / "input.partial"
        total = 0
        header = b""
        try:
            with partial.open("wb") as output:
                while chunk := await file.read(1 << 20):
                    if not header:
                        header = chunk[:16]
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise ValueError(
                            f"Upload exceeds the {MAX_UPLOAD_BYTES}-byte local limit"
                        )
                    output.write(chunk)
            if not total:
                raise ValueError("The uploaded file is empty")
            suffix = detect_suffix(header)
            input_path = job_dir / ("input" + suffix)
            partial.replace(input_path)
            manager.submit(job_id, input_path, predict_options)
        except QueueBusyError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (OSError, ValueError) as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            await file.close()
        return {"data": {"jobId": job_id}}

    @app.get("/api/v2/ocr/jobs/{job_id}/result")
    def fetch_result(job_id: str) -> FileResponse:
        try:
            path = manager.result_path(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Result not found") from exc
        # RAGFlow does not forward Authorization when it follows resultJsonUrl.
        return FileResponse(path, media_type="application/x-ndjson")

    @app.get("/api/v2/ocr/jobs/{job_id}")
    def poll_job(
        job_id: str, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        require_token(authorization)
        try:
            state = manager.status(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        return {"data": state}

    return app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--jobs-root", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=9399, type=int)
    parser.add_argument("--token", default=os.environ.get("LOCAL_OCR_TOKEN", "local"))
    parser.add_argument("--list-devices", action="store_true")
    args = parser.parse_args()
    if args.list_devices:
        return list_cuda_devices()
    for name in ("config", "model_root", "jobs_root"):
        if getattr(args, name) is None:
            raise SystemExit(f"--{name.replace('_', '-')} is required")
    if args.host != "127.0.0.1":
        raise SystemExit("The local OCR gateway may bind only to 127.0.0.1")

    pipeline, gpu_info = load_pipeline(args.config, args.model_root)
    max_block_tokens = parse_positive_int(
        os.environ.get("LOCAL_OCR_MAX_BLOCK_TOKENS", str(DEFAULT_MAX_BLOCK_TOKENS)).strip(),
        "LOCAL_OCR_MAX_BLOCK_TOKENS",
    )
    gpu_info["max_block_tokens"] = max_block_tokens
    manager = JobManager(
        pipeline,
        args.jobs_root,
        f"http://{args.host}:{args.port}",
        gpu_info,
        max_block_tokens=max_block_tokens,
    )
    app = create_app(manager, args.token)
    import uvicorn

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        manager.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
