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

import app


class DigitizerStartupTests(unittest.TestCase):
    def test_health_does_not_contact_external_services(self) -> None:
        source = inspect.getsource(app.create_app)
        health = source.split('@app.get("/health")', 1)[1].split(
            '@app.get("/api/config")', 1
        )[0]
        self.assertIn('"status": "ready"', health)
        self.assertNotIn("paddle", health)
        self.assertNotIn("llm", health)

    def test_pdf_picker_uses_owned_modern_dialog(self) -> None:
        selected = r"D:\docs\example.pdf"
        completed = subprocess.CompletedProcess(
            [],
            0,
            stdout=base64.b64encode(selected.encode("utf-8")) + b"\r\n",
            stderr=b"",
        )
        fake_os = SimpleNamespace(name="nt", environ={"SystemRoot": r"C:\Windows"})
        with (
            mock.patch.object(app, "os", fake_os),
            mock.patch.object(app.subprocess, "run", return_value=completed) as run,
        ):
            self.assertEqual(app.choose_pdf(), selected)
        script = base64.b64decode(run.call_args.args[0][-1]).decode("utf-16-le")
        self.assertIn("$d.AutoUpgradeEnabled=$true", script)
        self.assertIn("$d.ShowDialog($owner)", script)
        self.assertIn("$owner.TopMost=$true", script)
        self.assertNotIn("InitialDirectory", script)

    def test_start_script_keeps_portable_offline_defaults(self) -> None:
        script = Path(__file__).with_name("start.ps1").read_text(encoding="utf-8")
        self.assertIn("http://127.0.0.1:9399", script)
        self.assertIn("http://127.0.0.1:6381/v1", script)
        self.assertIn("PYTHONNOUSERSITE", script)
        self.assertNotIn("Start-Process $Url", script)

    def test_html_uses_dynamic_config_and_markdown_database(self) -> None:
        html = Path(__file__).with_name("app.html").read_text(encoding="utf-8")
        self.assertIn("await api('/api/config')", html)
        self.assertIn("digitizer:object", html)
        self.assertIn("digitizer:${region.kind}", html)
        self.assertIn("markdownEditor", html)
        self.assertIn("cellRect(region,row,column)", html)
        self.assertIn("scheduleAutoSave", html)
        self.assertIn("class=\"context-menu\"", html)
        self.assertIn("gridRail(region,'x')", html)
        self.assertIn("gridRail(region,'y')", html)
        self.assertNotIn('id="save"', html)
        self.assertNotIn("assignDialog", html)
        self.assertNotIn("gridColumns", html)
        self.assertNotIn("gridRows", html)
        self.assertNotIn("presets", html)
        self.assertNotIn("materialize", html)

    def test_cell_correction_resolves_before_selecting_destination_row(self) -> None:
        html = Path(__file__).with_name("app.html").read_text(encoding="utf-8")
        correction = html.index(
            "const corrected=normalizeCellText(await requestCorrection"
        )
        assignment = html.index("region._cells[row][column]=corrected")
        self.assertLess(correction, assignment)
        self.assertNotIn(
            "region._cells[row][column]=normalizeCellText(await requestCorrection",
            html,
        )

    def test_pdf_selection_remains_interactive_during_background_work(self) -> None:
        html = Path(__file__).with_name("app.html").read_text(encoding="utf-8")
        start_draw = html.split("function startDraw(event)", 1)[1].split(
            "function startRegionDrag", 1
        )[0]
        self.assertNotIn("state.busy", start_draw)
        self.assertIn("if(!state.drag&&!state.draft)renderPdf()", html)
        self.assertIn("ocrQueue:Promise.resolve()", html)
        self.assertIn("state.ocrQueue.then(()=>performOcr", html)

    def test_region_hover_actions_and_viewport_fitted_menus_are_present(self) -> None:
        html = Path(__file__).with_name("app.html").read_text(encoding="utf-8")
        self.assertIn("region-tools", html)
        self.assertIn("'↻ OCR'", html)
        self.assertIn("'✦ ИИ'", html)
        self.assertIn("'↻90°'", html)
        self.assertIn("'Удалить область и её текст'", html)
        self.assertIn("function positionRegionTools", html)
        self.assertIn("function positionSubmenu", html)
        self.assertIn("clampMenuPoint(x,box.width,innerWidth)", html)


if __name__ == "__main__":
    unittest.main()
