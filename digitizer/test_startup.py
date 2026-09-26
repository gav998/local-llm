from __future__ import annotations

import base64
import inspect
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import app  # noqa: E402


class DigitizerStartupTests(unittest.TestCase):
    def test_health_does_not_contact_ocr(self) -> None:
        source = inspect.getsource(app.create_app)
        health_route = source.split('@app.get("/health")', 1)[1].split(
            '@app.get("/api/catalog")', 1
        )[0]

        self.assertIn('"status": "ready"', health_route)
        self.assertNotIn("paddle.health", health_route)

    def test_pdf_picker_uses_modern_owned_dialog_without_forced_directory(self) -> None:
        selected = r"D:\docs\example.pdf"
        output = base64.b64encode(selected.encode("utf-8")) + b"\r\n"
        completed = subprocess.CompletedProcess([], 0, stdout=output, stderr=b"")
        fake_os = SimpleNamespace(name="nt", environ={"SystemRoot": r"C:\Windows"})

        with (
            mock.patch.object(app, "os", fake_os),
            mock.patch.object(app.subprocess, "run", return_value=completed) as run,
        ):
            self.assertEqual(app.choose_pdf(), selected)

        encoded_command = run.call_args.args[0][-1]
        script = base64.b64decode(encoded_command).decode("utf-16-le")
        self.assertIn("$d.AutoUpgradeEnabled=$true", script)
        self.assertIn("$d.ShowDialog($owner)", script)
        self.assertIn("$owner.TopMost=$true", script)
        self.assertNotIn("InitialDirectory", script)

    def test_start_script_has_offline_defaults_and_no_ocr_preflight(self) -> None:
        script = Path(__file__).with_name("start.ps1").read_text(encoding="utf-8")

        self.assertIn("http://127.0.0.1:9399", script)
        self.assertIn("http://127.0.0.1:6381/v1", script)
        self.assertNotIn("PaddleOCR is not running", script)
        self.assertNotIn("DIGITIZER_OPEN_DIRECTORY", script)
        self.assertNotIn("Start-Process $Url", script)

    def test_editor_source_preserves_focus_and_limits_object_actions(self) -> None:
        html = Path(__file__).with_name("app.html").read_text(encoding="utf-8")

        self.assertIn("function previewFocusSnapshot()", html)
        self.assertIn("restorePreviewFocus(focus)", html)
        self.assertIn("state.paneSyncPaused", html)
        self.assertIn("showObjectOcr=ocrSources.length>1", html)
        self.assertIn("background:var(--violet)", html)
        self.assertIn(".object-ocr{right:3px;top:3px;z-index:12;background:var(--blue)", html)
        self.assertNotIn("menuItem('Периодичность'", html)
        new_object_menu = html.index(
            "const presets=[menuItem('Текст',()=>withMenuAssignment"
        )
        organization = html.index("for(const preset of availablePresets())", new_object_menu)
        self.assertLess(new_object_menu, organization)


if __name__ == "__main__":
    unittest.main()
