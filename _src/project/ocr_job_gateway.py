#!/usr/bin/env python3
"""Local async PaddleOCR job API compatible with RAGFlow 0.27.1.

The stock Python RAGFlow parser calls ``/api/v2/ocr/jobs``. PaddleX basic
serving exposes a different synchronous endpoint, so this small loopback-only
gateway owns one strict-GPU PP-StructureV3 pipeline and adapts the protocol.
``LOCAL_OCR_GPU_INDEX`` selects the board and defaults to GPU 0.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import queue
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse


MODEL_NAMES = (
    "PP-DocLayout-L",
    "PP-DocBlockLayout",
    "PP-OCRv6_medium_det",
    "eslav_PP-OCRv5_mobile_rec",
    "SLANet_plus",
)
MAX_UPLOAD_BYTES = int(os.environ.get("LOCAL_OCR_MAX_UPLOAD_BYTES", str(512 << 20)))
ATOMIC_REPLACE_TIMEOUT_SECONDS = 2.0
ATOMIC_REPLACE_RETRY_SECONDS = 0.02

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


def load_strict_gpu_pipeline(config_path: Path, model_root: Path):
    if os.environ.get("PADDLE_PDX_DISABLE_DEVICE_FALLBACK") != "1":
        raise RuntimeError("PADDLE_PDX_DISABLE_DEVICE_FALLBACK must be 1")
    for name in MODEL_NAMES:
        for filename in ("inference.json", "inference.yml", "inference.pdiparams"):
            key = model_root / name / filename
            if key.is_symlink() or not key.is_file():
                raise RuntimeError(f"Prepared model is incomplete: {key}")

    import paddle
    import yaml

    if not paddle.is_compiled_with_cuda():
        raise RuntimeError("This PaddlePaddle build has no CUDA support")

    compiled_cuda = str(paddle.version.cuda)
    if compiled_cuda != "11.8":
        raise RuntimeError(
            f"This runtime requires the pinned CUDA 11.8 wheel, got CUDA {compiled_cuda}"
        )

    count = paddle.device.cuda.device_count()
    if count < 1:
        raise RuntimeError("No CUDA device is visible to PaddlePaddle")

    gpu_index_text = os.environ.get("LOCAL_OCR_GPU_INDEX", "0").strip()
    try:
        gpu_index = int(gpu_index_text)
    except ValueError as exc:
        raise RuntimeError(
            f"LOCAL_OCR_GPU_INDEX must be an integer, got {gpu_index_text!r}"
        ) from exc
    if gpu_index < 0 or gpu_index >= count:
        raise RuntimeError(
            f"LOCAL_OCR_GPU_INDEX={gpu_index} is outside the visible GPU range 0..{count - 1}"
        )

    device = f"gpu:{gpu_index}"
    paddle.set_device(device)
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
    checksum = float(paddle.sum(paddle.matmul(left, right)).numpy().item())

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    validate_pipeline_profile(config)
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
    table_config["SubModules"]["TableStructureRecognition"]["model_dir"] = str(
        model_root / "SLANet_plus"
    )

    from paddleocr import PPStructureV3

    pipeline = PPStructureV3(paddlex_config=config, device=device)
    gpu_info = {
        "strict_gpu": True,
        "paddle_version": paddle.__version__,
        "paddle_cuda_version": compiled_cuda,
        "cuda_device_count": count,
        "gpu_index": gpu_index,
        "gpu_device": device,
        "gpu_capability": ".".join(str(part) for part in capability),
        "compiled_cuda_arches": compiled_arches,
        "cuda_tensor_checksum": checksum,
        "table_recognition": True,
        "table_structure_model": "SLANet_plus",
    }
    return pipeline, gpu_info


class JobManager:
    def __init__(
        self,
        pipeline: Any,
        jobs_root: Path,
        public_url: str,
        gpu_info: dict[str, Any],
        max_queued_jobs: int = 8,
    ) -> None:
        self.pipeline = pipeline
        self.jobs_root = jobs_root
        self.public_url = public_url.rstrip("/")
        self.gpu_info = gpu_info
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self._queue: queue.Queue[tuple[str, Path, dict[str, Any]] | None] = queue.Queue(
            maxsize=max_queued_jobs
        )
        self._closed = False
        self._recover_interrupted_jobs()
        self._worker = threading.Thread(target=self._run, name="ocr-gpu-0", daemon=True)
        self._worker.start()

    def _state_path(self, job_id: str) -> Path:
        return self.jobs_root / job_id / "state.json"

    def _result_path(self, job_id: str) -> Path:
        return self.jobs_root / job_id / "result.jsonl"

    def _recover_interrupted_jobs(self) -> None:
        for state_path in self.jobs_root.glob("*/state.json"):
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
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
        if not path.is_file():
            raise KeyError(job_id)
        state = json.loads(path.read_text(encoding="utf-8"))
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
            state = json.loads(state_path.read_text(encoding="utf-8"))
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
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--jobs-root", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=9399, type=int)
    parser.add_argument("--token", default=os.environ.get("LOCAL_OCR_TOKEN", "local"))
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        raise SystemExit("The local OCR gateway may bind only to 127.0.0.1")

    pipeline, gpu_info = load_strict_gpu_pipeline(args.config, args.model_root)
    manager = JobManager(
        pipeline,
        args.jobs_root,
        f"http://{args.host}:{args.port}",
        gpu_info,
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
