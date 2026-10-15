from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_launcher_keeps_runtime_portable_and_gpu_only():
    script = (ROOT / "start.ps1").read_text(encoding="utf-8-sig")
    assert "$env:HF_HOME" in script
    assert "$env:TRANSFORMERS_CACHE" in script
    assert "$env:TEMP" in script
    assert "torch==2.5.1+cu118" in script
    assert "torch.cuda.is_available()" in script
    assert "insightface" in script
    assert "onnxruntime" in script
    assert "faceid-plusv2" in script
    assert "-NoNewWindow" in script


def test_faceid_runtime_dependencies_are_pinned():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "onnx==1.17.0" in requirements
    assert "onnxruntime==1.20.1" in requirements
    assert "peft==0.14.0" in requirements
    assert "insightface-0.7.3-cp311-cp311-win_amd64.whl" in requirements


def test_runtime_directories_are_ignored():
    ignore = (ROOT.parent / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("**/_runtime/", "**/_download/", "**/_cache/", "**/_profile/", "**/_temp/", "**/data/"):
        assert pattern in ignore
    assert "image-editor/models/" in ignore
