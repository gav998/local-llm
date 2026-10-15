"""Loopback-only inpainting API with IP-Adapter FaceID identity guidance."""

from __future__ import annotations

import argparse
import io
import json
import os
import secrets
import threading
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageFilter, ImageOps


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.json"
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
ALLOWED_IMAGE_FORMATS = {"PNG", "JPEG", "WEBP", "BMP", "TIFF"}

# Every cache used by Hugging Face, Torch and temporary downloads remains portable.
os.environ.setdefault("HF_HOME", str(ROOT / "models" / "huggingface"))
os.environ.setdefault("HF_HUB_CACHE", str(ROOT / "models" / "huggingface" / "hub"))
os.environ.setdefault("TRANSFORMERS_CACHE", str(ROOT / "models" / "huggingface" / "hub"))
os.environ.setdefault("TORCH_HOME", str(ROOT / "models" / "torch"))
os.environ.setdefault("XDG_CACHE_HOME", str(ROOT / "_cache"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    api_key: str
    gpu_index: int
    resolution: int
    model_id: str
    model_revision: str
    adapter_id: str
    adapter_subfolder: str
    adapter_weight: str
    image_encoder_id: str
    face_analyzer_name: str
    face_detection_size: int
    faceid_shortcut_scale: float
    crop_padding_ratio: float
    mask_blur_radius: float
    cpu_offload: bool
    default_steps: int
    default_guidance_scale: float
    default_ip_adapter_scale: float
    default_strength: float
    default_prompt: str
    default_negative_prompt: str


def load_settings(path: Path = DEFAULT_CONFIG, **overrides: Any) -> Settings:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    raw.update({key: value for key, value in overrides.items() if value is not None})
    settings = Settings(**raw)
    if settings.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("image-editor разрешено слушать только loopback-адрес")
    if not 1 <= settings.port <= 65535:
        raise ValueError("port должен быть от 1 до 65535")
    if not settings.api_key:
        raise ValueError("api_key не может быть пустым")
    if settings.gpu_index < 0:
        raise ValueError("gpu_index не может быть отрицательным")
    if settings.resolution < 256 or settings.resolution > 768 or settings.resolution % 8:
        raise ValueError("resolution должен быть кратен 8 и находиться между 256 и 768")
    if settings.face_detection_size < 320 or settings.face_detection_size > 1280:
        raise ValueError("face_detection_size должен быть от 320 до 1280")
    if not 0 <= settings.faceid_shortcut_scale <= 2:
        raise ValueError("faceid_shortcut_scale должен быть от 0 до 2")
    if not 0 <= settings.crop_padding_ratio <= 4:
        raise ValueError("crop_padding_ratio должен быть от 0 до 4")
    if not 0 <= settings.mask_blur_radius <= 32:
        raise ValueError("mask_blur_radius должен быть от 0 до 32")
    return settings


def _open_image(payload: bytes, label: str) -> Image.Image:
    if not payload:
        raise HTTPException(400, f"{label}: пустой файл")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{label}: файл больше {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ")
    try:
        with Image.open(io.BytesIO(payload)) as source:
            if source.format not in ALLOWED_IMAGE_FORMATS:
                raise HTTPException(415, f"{label}: неподдерживаемый формат {source.format}")
            return ImageOps.exif_transpose(source).copy()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"{label}: не удалось прочитать изображение") from exc


def _fit_for_model(image: Image.Image, size: int, mode: str) -> tuple[Image.Image, tuple[int, int, int, int]]:
    width, height = image.size
    scale = min(size / width, size / height)
    resized = image.resize(
        (max(1, round(width * scale)), max(1, round(height * scale))),
        Image.Resampling.LANCZOS if mode != "L" else Image.Resampling.BILINEAR,
    )
    left = (size - resized.width) // 2
    top = (size - resized.height) // 2
    canvas = Image.new(mode, (size, size), 0 if mode == "L" else (255, 255, 255))
    canvas.paste(resized.convert(mode), (left, top))
    return canvas, (left, top, resized.width, resized.height)


def _restore_from_model(image: Image.Image, box: tuple[int, int, int, int], size: tuple[int, int]) -> Image.Image:
    left, top, width, height = box
    return image.crop((left, top, left + width, top + height)).resize(size, Image.Resampling.LANCZOS)


def _centered_interval(center: float, length: int, limit: int) -> tuple[int, int]:
    length = min(max(1, length), limit)
    start = round(center - length / 2)
    start = min(max(0, start), limit - length)
    return start, start + length


def _crop_box_from_mask(mask: Image.Image, padding_ratio: float) -> tuple[int, int, int, int]:
    """Return a mask-centered crop so a small face is enlarged before inference."""
    bbox = mask.getbbox()
    if bbox is None:
        raise ValueError("маска полностью чёрная")
    left, top, right, bottom = bbox
    mask_width = right - left
    mask_height = bottom - top
    desired_side = round(max(mask_width, mask_height) * (1 + 2 * padding_ratio))
    crop_width = min(mask.width, max(mask_width, desired_side))
    crop_height = min(mask.height, max(mask_height, desired_side))
    crop_left, crop_right = _centered_interval((left + right) / 2, crop_width, mask.width)
    crop_top, crop_bottom = _centered_interval((top + bottom) / 2, crop_height, mask.height)
    return crop_left, crop_top, crop_right, crop_bottom


def _ip_adapter_attention_processor_count(pipeline: Any) -> int:
    processors = getattr(getattr(pipeline, "unet", None), "attn_processors", {})
    return sum("IPAdapter" in type(processor).__name__ for processor in processors.values())


def _configure_pipeline(pipeline: Any, settings: Settings) -> None:
    """Load FaceID Plus v2 without subsequently replacing its attention processors."""
    pipeline.enable_vae_slicing()
    print(f"[MODEL] Загрузка адаптера {settings.adapter_weight}...", flush=True)
    pipeline.load_ip_adapter(
        settings.adapter_id,
        subfolder=settings.adapter_subfolder or None,
        weight_name=settings.adapter_weight,
        image_encoder_folder=None,
        cache_dir=str(ROOT / "models" / "huggingface"),
    )

    # enable_attention_slicing() must not be called after load_ip_adapter(): it
    # replaces IPAdapterAttnProcessor with a generic sliced processor. Diffusers
    # then sends the IP-Adapter (text, image) tuple to code that expects a tensor
    # and generation fails with: 'tuple' object has no attribute 'shape'.
    processor_count = _ip_adapter_attention_processor_count(pipeline)
    if processor_count == 0:
        raise RuntimeError("IP-Adapter загружен без attention-процессоров")
    print(f"[MODEL] Активно IP-Adapter attention-процессоров: {processor_count}", flush=True)

    projection_layers = pipeline.unet.encoder_hid_proj.image_projection_layers
    if len(projection_layers) != 1 or not hasattr(projection_layers[0], "shortcut"):
        raise RuntimeError("загружен несовместимый адаптер: ожидался FaceID Plus v2")
    projection_layers[0].shortcut = True
    projection_layers[0].shortcut_scale = settings.faceid_shortcut_scale

    if settings.cpu_offload:
        pipeline.enable_model_cpu_offload(gpu_id=settings.gpu_index)
    else:
        pipeline.to(f"cuda:{settings.gpu_index}")


class ImageEditorService:
    """Owns one pipeline and serializes requests to keep VRAM use bounded."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.pipeline: Any | None = None
        self.torch: Any | None = None
        self.face_analyzer: Any | None = None
        self.device_name = "not loaded"
        self.vram_bytes = 0
        self.error: str | None = None
        self.lock = threading.Lock()

    def load(self) -> None:
        print("[MODEL] Проверка CUDA и подготовка моделей...", flush=True)
        try:
            import torch
            from diffusers import DDIMScheduler, StableDiffusionInpaintPipeline
            from insightface.app import FaceAnalysis
            from transformers import CLIPVisionModelWithProjection

            if not torch.cuda.is_available():
                raise RuntimeError("CUDA недоступна; CPU fallback намеренно отключён")
            if self.settings.gpu_index >= torch.cuda.device_count():
                raise RuntimeError(
                    f"GPU {self.settings.gpu_index} не существует; доступно карт: {torch.cuda.device_count()}"
                )
            properties = torch.cuda.get_device_properties(self.settings.gpu_index)
            if properties.major < 6:
                raise RuntimeError("нужна NVIDIA GPU с compute capability 6.0 или новее")
            self.device_name = properties.name
            self.vram_bytes = properties.total_memory
            print(
                f"[GPU] cuda:{self.settings.gpu_index}: {self.device_name}, "
                f"{self.vram_bytes / (1024 ** 3):.1f} ГБ VRAM",
                flush=True,
            )
            print(
                f"[FACEID] Загрузка InsightFace {self.settings.face_analyzer_name} "
                "(анализ референса выполняется на CPU)...",
                flush=True,
            )
            face_analyzer = FaceAnalysis(
                name=self.settings.face_analyzer_name,
                root=str(ROOT / "models" / "insightface"),
                allowed_modules=["detection", "recognition"],
                providers=["CPUExecutionProvider"],
            )
            face_analyzer.prepare(
                ctx_id=-1,
                det_size=(self.settings.face_detection_size, self.settings.face_detection_size),
            )
            print(f"[MODEL] Загрузка CLIP image encoder {self.settings.image_encoder_id}...", flush=True)
            image_encoder = CLIPVisionModelWithProjection.from_pretrained(
                self.settings.image_encoder_id,
                torch_dtype=torch.float16,
                cache_dir=str(ROOT / "models" / "huggingface"),
                use_safetensors=True,
            )
            print(f"[MODEL] Загрузка {self.settings.model_id} (первый запуск скачивает файлы)...", flush=True)
            pipeline = StableDiffusionInpaintPipeline.from_pretrained(
                self.settings.model_id,
                revision=self.settings.model_revision or None,
                variant="fp16",
                torch_dtype=torch.float16,
                cache_dir=str(ROOT / "models" / "huggingface"),
                use_safetensors=True,
                image_encoder=image_encoder,
            )
            pipeline.scheduler = DDIMScheduler.from_config(pipeline.scheduler.config)
            _configure_pipeline(pipeline, self.settings)
            self.pipeline = pipeline
            self.torch = torch
            self.face_analyzer = face_analyzer
            print("[MODEL] Модель готова.", flush=True)
        except Exception as exc:
            self.error = str(exc)
            print(f"[MODEL ERROR] {exc}", flush=True)
            raise

    @property
    def ready(self) -> bool:
        return self.pipeline is not None and self.face_analyzer is not None and self.error is None

    def _prepare_faceid_condition(
        self,
        reference: Image.Image,
        guidance_scale: float,
        request_id: str,
    ) -> list[Any]:
        if self.pipeline is None or self.torch is None or self.face_analyzer is None:
            raise RuntimeError("FaceID ещё не загружен")
        import cv2
        import numpy as np
        from insightface.utils import face_align

        reference_rgb = np.asarray(reference.convert("RGB")).copy()
        reference_bgr = cv2.cvtColor(reference_rgb, cv2.COLOR_RGB2BGR)
        faces = self.face_analyzer.get(reference_bgr)
        if not faces:
            raise ValueError(
                "на reference_image не найдено лицо; используйте чёткий снимок одного человека анфас"
            )
        face = max(
            faces,
            key=lambda candidate: max(0, candidate.bbox[2] - candidate.bbox[0])
            * max(0, candidate.bbox[3] - candidate.bbox[1]),
        )
        aligned_bgr = face_align.norm_crop(reference_bgr, landmark=face.kps, image_size=224)
        aligned = Image.fromarray(cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2RGB))
        bbox = tuple(round(float(value), 1) for value in face.bbox)
        print(f"[FACEID {request_id}] лицо найдено, bbox={bbox}", flush=True)

        device = self.torch.device(f"cuda:{self.settings.gpu_index}")
        use_guidance = guidance_scale > 1
        face_embedding = self.torch.from_numpy(
            np.asarray(face.normed_embedding, dtype=np.float32)
        ).reshape(1, 1, 1, -1)
        if use_guidance:
            face_embedding = self.torch.cat([self.torch.zeros_like(face_embedding), face_embedding])
        face_embedding = face_embedding.to(device=device, dtype=self.torch.float16)

        with self.torch.inference_mode():
            clip_embedding = self.pipeline.prepare_ip_adapter_image_embeds(
                [[aligned]],
                None,
                device,
                1,
                use_guidance,
            )[0]
        projection = self.pipeline.unet.encoder_hid_proj.image_projection_layers[0]
        projection.clip_embeds = clip_embedding.to(device=device, dtype=self.torch.float16)
        return [face_embedding]

    def edit(
        self,
        image: Image.Image,
        mask: Image.Image,
        reference: Image.Image,
        prompt: str,
        negative_prompt: str,
        steps: int,
        guidance_scale: float,
        ip_adapter_scale: float,
        strength: float,
        seed: int,
        request_id: str,
    ) -> Image.Image:
        if not self.ready or self.torch is None:
            raise RuntimeError(self.error or "модель ещё не загружена")
        original = image.convert("RGBA")
        original_size = original.size
        mask = mask.convert("L").resize(original_size, Image.Resampling.BILINEAR)
        crop_box = _crop_box_from_mask(mask, self.settings.crop_padding_ratio)
        base_image = Image.alpha_composite(Image.new("RGBA", original_size, "white"), original).convert("RGB")
        cropped_image = base_image.crop(crop_box)
        cropped_mask = mask.crop(crop_box)
        model_image, content_box = _fit_for_model(
            cropped_image,
            self.settings.resolution,
            "RGB",
        )
        model_mask, _ = _fit_for_model(cropped_mask, self.settings.resolution, "L")
        softened_mask = model_mask.filter(ImageFilter.GaussianBlur(radius=self.settings.mask_blur_radius))

        def progress_callback(_pipeline: Any, step_index: int, _timestep: Any, callback_kwargs: dict[str, Any]):
            print(f"[GENERATE {request_id}] шаг {step_index + 1}/{steps}", flush=True)
            return callback_kwargs

        with self.lock:
            self.pipeline.set_ip_adapter_scale(ip_adapter_scale)
            faceid_embeds = self._prepare_faceid_condition(reference, guidance_scale, request_id)
            started = time.monotonic()
            print(
                f"[GENERATE {request_id}] старт: {steps} шагов, seed={seed}, "
                f"GPU={self.settings.gpu_index}, crop={crop_box}",
                flush=True,
            )
            generator = self.torch.Generator(device="cpu").manual_seed(seed)
            result = self.pipeline(
                prompt=prompt,
                negative_prompt=negative_prompt,
                image=model_image,
                mask_image=model_mask,
                ip_adapter_image_embeds=faceid_embeds,
                num_inference_steps=steps,
                guidance_scale=guidance_scale,
                strength=strength,
                generator=generator,
                callback_on_step_end=progress_callback,
                callback_on_step_end_tensor_inputs=["latents"],
            ).images[0].convert("RGB")
            duration = time.monotonic() - started
            print(f"[GENERATE {request_id}] готово за {duration:.1f} с", flush=True)

        blended = Image.composite(result, model_image, softened_mask)
        crop_size = (crop_box[2] - crop_box[0], crop_box[3] - crop_box[1])
        restored_crop = _restore_from_model(blended, content_box, crop_size)
        restored = base_image.copy()
        restored.paste(restored_crop, crop_box[:2])
        restored.putalpha(original.getchannel("A"))
        return restored


def create_app(settings: Settings, service: ImageEditorService | None = None, load_model: bool = True) -> FastAPI:
    service = service or ImageEditorService(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if load_model:
            service.load()
        yield

    app = FastAPI(title="Local Image Editor", version="2.0", lifespan=lifespan)

    def authorize(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {settings.api_key}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(401, "неверный API key", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ready" if service.ready else "loading" if not service.error else "error",
            "model": settings.model_id,
            "adapter": settings.adapter_weight,
            "face_analyzer": settings.face_analyzer_name,
            "crop_padding_ratio": settings.crop_padding_ratio,
            "device": f"cuda:{settings.gpu_index}",
            "device_name": service.device_name,
            "vram_gb": round(service.vram_bytes / (1024 ** 3), 2),
            "cpu_offload": settings.cpu_offload,
            "error": service.error,
        }

    @app.post("/v1/images/edits", dependencies=[Depends(authorize)])
    def edit_image(
        image: UploadFile = File(...),
        mask: UploadFile = File(...),
        reference_image: UploadFile = File(...),
        prompt: str = Form(default=""),
        negative_prompt: str = Form(default=""),
        steps: int | None = Form(default=None),
        guidance_scale: float | None = Form(default=None),
        ip_adapter_scale: float | None = Form(default=None),
        strength: float | None = Form(default=None),
        seed: int = Form(default=-1),
    ) -> Response:
        if not service.ready:
            raise HTTPException(503, service.error or "модель ещё загружается")
        steps = settings.default_steps if steps is None else steps
        guidance_scale = settings.default_guidance_scale if guidance_scale is None else guidance_scale
        ip_adapter_scale = settings.default_ip_adapter_scale if ip_adapter_scale is None else ip_adapter_scale
        strength = settings.default_strength if strength is None else strength
        if not 1 <= steps <= 80:
            raise HTTPException(422, "steps должен быть от 1 до 80")
        if not 1 <= guidance_scale <= 20:
            raise HTTPException(422, "guidance_scale должен быть от 1 до 20")
        if not 0 <= ip_adapter_scale <= 1.5:
            raise HTTPException(422, "ip_adapter_scale должен быть от 0 до 1.5")
        if not 0 < strength <= 1:
            raise HTTPException(422, "strength должен быть больше 0 и не больше 1")
        if seed < 0:
            seed = secrets.randbelow(2**31)
        if seed >= 2**63:
            raise HTTPException(422, "seed слишком большой")

        request_id = uuid.uuid4().hex[:8]
        try:
            target = _open_image(image.file.read(MAX_UPLOAD_BYTES + 1), "image")
            target_mask = _open_image(mask.file.read(MAX_UPLOAD_BYTES + 1), "mask")
            reference = _open_image(reference_image.file.read(MAX_UPLOAD_BYTES + 1), "reference_image")
            result = service.edit(
                target,
                target_mask,
                reference,
                prompt.strip() or settings.default_prompt,
                negative_prompt.strip() or settings.default_negative_prompt,
                steps,
                guidance_scale,
                ip_adapter_scale,
                strength,
                seed,
                request_id,
            )
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            print(f"[GENERATE {request_id}] ошибка: {exc}", flush=True)
            traceback.print_exc()
            raise HTTPException(500, f"ошибка генерации: {exc}") from exc

        output = io.BytesIO()
        result.save(output, format="PNG")
        return Response(
            output.getvalue(),
            media_type="image/png",
            headers={"X-Request-Id": request_id, "X-Seed": str(seed)},
        )

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Локальный GPU API редактирования изображений")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--api-key")
    parser.add_argument("--gpu-index", type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings(
        host=args.host,
        port=args.port,
        api_key=args.api_key,
        gpu_index=args.gpu_index,
    )
    import uvicorn

    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
