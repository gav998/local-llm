from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_launcher_keeps_runtime_portable_and_gpu_only():
    script = (ROOT / "start.ps1").read_text(encoding="utf-8-sig")
    assert "$env:HF_HOME" in script
    assert "$env:TRANSFORMERS_CACHE" in script
    assert "$env:TEMP" in script
    assert "torch==2.5.1+cu118" in script
    assert "torch.cuda.is_available()" in script
    assert "-NoNewWindow" in script


def test_runtime_directories_are_ignored():
    ignore = (ROOT.parent / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("**/_runtime/", "**/_download/", "**/_cache/", "**/_profile/", "**/_temp/", "**/data/"):
        assert pattern in ignore
    assert "image-editor/models/" in ignore
