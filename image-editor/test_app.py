from __future__ import annotations

import io
from pathlib import Path

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
