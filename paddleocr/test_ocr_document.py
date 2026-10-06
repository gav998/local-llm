from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import httpx

import ocr_document


class OcrDocumentTests(unittest.TestCase):
    def test_output_replaces_source_extension(self) -> None:
        self.assertEqual(
            ocr_document.output_path_for(Path("report.scan.pdf")),
            Path("report.scan.md"),
        )

    def test_asset_path_rejects_escape(self) -> None:
        with self.assertRaises(ValueError):
            ocr_document.normalized_asset_path("../outside.png")

    def test_submit_uses_async_pp_structure_queue(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/api/v2/ocr/jobs")
            self.assertEqual(request.headers["authorization"], "Bearer secret")
            self.assertIn(b'PP-StructureV3', request.content)
            return httpx.Response(200, json={"data": {"jobId": "job-1"}})

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scan.pdf"
            source.write_bytes(b"%PDF-1.7")
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                job_id = ocr_document.submit_job(
                    client, "http://127.0.0.1:9399", "secret", source
                )
        self.assertEqual(job_id, "job-1")

    def test_export_writes_markdown_and_local_assets(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                request.url.path,
                "/api/v2/ocr/jobs/job-1/assets/img/table 1.png",
            )
            return httpx.Response(200, content=b"image-bytes")

        records = [
            {
                "result": {
                    "markdown": {
                        "text": "# Отчёт\n\n![](img/table 1.png)",
                        "assets": ["img/table 1.png"],
                    }
                }
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "scan.md"
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                ocr_document.export_markdown(
                    client, "http://127.0.0.1:9399", records, output, "job-1"
                )

            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "# Отчёт\n\n![](scan_assets/img/table%201.png)\n",
            )
            self.assertEqual(
                (output.parent / "scan_assets" / "img" / "table 1.png").read_bytes(),
                b"image-bytes",
            )

    def test_fallback_markdown_uses_layout_blocks(self) -> None:
        result = {
            "layoutParsingResults": [
                {
                    "prunedResult": {
                        "parsing_res_list": [
                            {"block_content": "Первый"},
                            {"block_content": "Второй"},
                        ]
                    }
                }
            ]
        }
        self.assertEqual(ocr_document.fallback_markdown(result), "Первый\n\nВторой")

    def test_start_scripts_queue_dropped_files_until_profile_selection(self) -> None:
        root = Path(__file__).parent
        powershell = (root / "start.ps1").read_text(encoding="utf-8-sig")
        batch = (root / "start.bat").read_text(encoding="utf-8")

        self.assertIn("ValueFromRemainingArguments=$true", powershell)
        self.assertIn("$PendingInput = @($InputPath)", powershell)
        self.assertIn("Start-Profile $Profiles[$Number - 1].Id", powershell)
        self.assertIn("Invoke-DocumentOcr $Documents", powershell)
        self.assertNotIn("Start-Profile 'full-structure'", powershell)
        self.assertIn('start.ps1" %*', batch)

    def test_menu_accepts_a_document_path_for_the_running_profile(self) -> None:
        powershell = (Path(__file__).parent / "start.ps1").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn("абсолютный/относительный путь", powershell)
        self.assertIn("Resolve-DocumentInputs @($Choice)", powershell)
        self.assertIn("Сначала выберите и запустите профиль OCR.", powershell)

    def test_document_start_message_includes_its_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scan.pdf"
            source.write_bytes(b"%PDF-1.7")
            output = io.StringIO()
            with (
                mock.patch.object(ocr_document, "submit_job", return_value="job-1"),
                mock.patch.object(ocr_document, "wait_for_job"),
                mock.patch.object(ocr_document, "fetch_result_records", return_value=[]),
                mock.patch.object(ocr_document, "export_markdown"),
                redirect_stdout(output),
            ):
                ocr_document.recognize_document(
                    mock.Mock(), "http://127.0.0.1:9399", "secret", source
                )

        self.assertIn(f"Начато OCR документа: {source}", output.getvalue())

    def test_main_continues_after_one_document_fails(self) -> None:
        recognized = [ValueError("bad document"), Path("good.md")]
        with mock.patch.object(
            ocr_document, "recognize_document", side_effect=recognized
        ) as recognize:
            result = ocr_document.main(
                [
                    "--url",
                    "http://127.0.0.1:9399",
                    "--token",
                    "secret",
                    "bad.pdf",
                    "good.pdf",
                ]
            )

        self.assertEqual(result, 1)
        self.assertEqual(recognize.call_count, 2)


if __name__ == "__main__":
    unittest.main()
