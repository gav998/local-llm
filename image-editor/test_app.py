from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image

import app


def png(mode: str = "RGB", color=(20, 30, 40)) -> bytes:
    output = io.BytesIO()
    Image.new(mode, (32, 24), color).save(output, "PNG")
    return output.getvalue()


class FakeService:
    ready = True
    error = None
    device_name = "fake GTX 1080"
    vram_bytes = 8 * 1024**3

    def edit(self, image, mask, reference, *args):
        assert image.size == (32, 24)
        assert mask.getbbox() is not None
        assert reference.size == (32, 24)
        return Image.new("RGBA", image.size, (1, 2, 3, 255))


def settings() -> app.Settings:
    return app.load_settings(Path(__file__).with_name("config.json"))


def test_health_and_authenticated_edit():
    client = TestClient(app.create_app(settings(), FakeService(), load_model=False))
    assert client.get("/health").json()["status"] == "ready"
    files = {
        "image": ("image.png", png(), "image/png"),
        "mask": ("mask.png", png("L", 255), "image/png"),
        "reference_image": ("reference.png", png(), "image/png"),
    }
    response = client.post(
        "/v1/images/edits",
        files=files,
        headers={"Authorization": "Bearer local-image-editor"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(response.content)).size == (32, 24)


def test_edit_rejects_missing_key():
    client = TestClient(app.create_app(settings(), FakeService(), load_model=False))
    response = client.post("/v1/images/edits")
    assert response.status_code == 401


def test_settings_reject_non_loopback(tmp_path):
    config = Path(__file__).with_name("config.json").read_text(encoding="utf-8")
    candidate = tmp_path / "config.json"
    candidate.write_text(config.replace('"127.0.0.1"', '"0.0.0.0"'), encoding="utf-8")
    try:
        app.load_settings(candidate)
    except ValueError as exc:
        assert "loopback" in str(exc)
    else:
        raise AssertionError("non-loopback host was accepted")


def test_default_settings_use_faceid_plus_v2():
    configured = settings()
    assert configured.model_id == "Lykon/dreamshaper-8-inpainting"
    assert configured.adapter_weight == "ip-adapter-faceid-plusv2_sd15.bin"
    assert configured.image_encoder_id == "laion/CLIP-ViT-H-14-laion2B-s32B-b79K"
    assert configured.default_strength < 0.98


def test_mask_crop_enlarges_small_face_and_stays_inside_slide():
    mask = Image.new("L", (1920, 1080), 0)
    mask.paste(255, (900, 450, 980, 550))

    crop = app._crop_box_from_mask(mask, padding_ratio=1.0)

    assert crop == (790, 350, 1090, 650)
    assert crop[0] <= 900 and crop[1] <= 450
    assert crop[2] >= 980 and crop[3] >= 550


def test_mask_crop_shifts_at_image_edge_without_clipping_mask():
    mask = Image.new("L", (400, 300), 0)
    mask.paste(255, (0, 10, 60, 90))

    crop = app._crop_box_from_mask(mask, padding_ratio=1.0)

    assert crop == (0, 0, 240, 240)


def test_mask_crop_rejects_empty_mask():
    try:
        app._crop_box_from_mask(Image.new("L", (100, 100), 0), padding_ratio=1.0)
    except ValueError as exc:
        assert "чёрная" in str(exc)
    else:
        raise AssertionError("empty mask was accepted")


def test_edit_runs_on_mask_crop_and_preserves_pixels_outside_it():
    calls = {}

    class FakeGenerator:
        def __init__(self, device):
            assert device == "cpu"

        def manual_seed(self, seed):
            calls["seed"] = seed
            return self

    class FakeTorch:
        Generator = FakeGenerator

    class FakePipeline:
        def set_ip_adapter_scale(self, scale):
            calls["scale"] = scale

        def __call__(self, **kwargs):
            calls["model_size"] = kwargs["image"].size
            calls["mask_size"] = kwargs["mask_image"].size
            calls["faceid"] = kwargs["ip_adapter_image_embeds"]
            return SimpleNamespace(images=[Image.new("RGB", kwargs["image"].size, "red")])

    service = app.ImageEditorService(settings())
    service.pipeline = FakePipeline()
    service.torch = FakeTorch()
    service.face_analyzer = object()
    service._prepare_faceid_condition = lambda *_args: ["faceid"]

    source = Image.new("RGB", (640, 360), "blue")
    mask = Image.new("L", source.size, 0)
    mask.paste(255, (290, 140, 350, 220))
    result = service.edit(
        source,
        mask,
        Image.new("RGB", (64, 64), "white"),
        "prompt",
        "negative",
        5,
        6.5,
        0.8,
        0.9,
        42,
        "test",
    )

    assert result.size == source.size
    assert result.getpixel((10, 10))[:3] == (0, 0, 255)
    assert result.getpixel((320, 180))[:3] == (255, 0, 0)
    assert calls == {
        "scale": 0.8,
        "seed": 42,
        "model_size": (512, 512),
        "mask_size": (512, 512),
        "faceid": ["faceid"],
    }


def test_pipeline_configuration_preserves_ip_adapter_attention_processors():
    actions = []

    class IPAdapterAttnProcessor2_0:
        pass

    projection = SimpleNamespace(shortcut=False, shortcut_scale=0.0)

    class FakePipeline:
        unet = SimpleNamespace(
            attn_processors={},
            encoder_hid_proj=SimpleNamespace(image_projection_layers=[projection]),
        )

        def enable_vae_slicing(self):
            actions.append("vae_slicing")

        def load_ip_adapter(self, *args, **kwargs):
            actions.append("ip_adapter")
            self.unet.attn_processors = {"down.attn2": IPAdapterAttnProcessor2_0()}

        def enable_attention_slicing(self, *args, **kwargs):
            raise AssertionError("attention slicing would replace IP-Adapter processors")

        def enable_model_cpu_offload(self, *, gpu_id):
            actions.append(("cpu_offload", gpu_id))

    pipeline = FakePipeline()
    app._configure_pipeline(pipeline, settings())

    assert actions == ["vae_slicing", "ip_adapter", ("cpu_offload", 0)]
    assert app._ip_adapter_attention_processor_count(pipeline) == 1
    assert projection.shortcut is True
    assert projection.shortcut_scale == 1.0


def test_pipeline_configuration_rejects_missing_ip_adapter_processors():
    class FakePipeline:
        unet = SimpleNamespace(
            attn_processors={"down.attn2": object()},
            encoder_hid_proj=SimpleNamespace(image_projection_layers=[]),
        )

        def enable_vae_slicing(self):
            pass

        def load_ip_adapter(self, *args, **kwargs):
            pass

    try:
        app._configure_pipeline(FakePipeline(), settings())
    except RuntimeError as exc:
        assert "attention-процессоров" in str(exc)
    else:
        raise AssertionError("pipeline without IP-Adapter processors was accepted")
