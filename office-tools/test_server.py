from __future__ import annotations

import asyncio
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server
from docx import Document
from mcp import Client

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class OfficeMcpServerTests(unittest.TestCase):
    def test_expected_tools_register_and_are_callable(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "document.docx"
                workbook = Path(directory) / "created.xlsx"
                document = Document()
                document.add_paragraph("Тест")
                document.save(source)
                async with Client(server.mcp) as client:
                    listing = await client.list_tools()
                    names = {tool.name for tool in listing.tools}
                    self.assertEqual(
                        names,
                        {
                            "inspect_word",
                            "inspect_excel",
                            "replace_word_text",
                            "set_excel_cells",
                            "create_word",
                            "create_excel",
                        },
                    )
                    response = await client.call_tool(
                        "inspect_word", {"path": str(source)}
                    )
                    self.assertFalse(response.is_error)
                    self.assertEqual(response.structured_content["type"], "docx")
                    self.assertEqual(
                        response.structured_content["paragraphs"][0]["text"], "Тест"
                    )
                    created = await client.call_tool(
                        "create_excel",
                        {
                            "output_path": str(workbook),
                            "sheet": "Data",
                            "rows": [["name", "value"], ["item", 41]],
                        },
                    )
                    self.assertFalse(created.is_error)
                    changed = await client.call_tool(
                        "set_excel_cells",
                        {
                            "input_path": str(workbook),
                            "output_path": str(Path(directory) / "edited.xlsx"),
                            "sheet": "Data",
                            "assignments": {"B2": 42, "C2": "=B2*2"},
                        },
                    )
                    self.assertFalse(changed.is_error)
                    self.assertEqual(
                        changed.structured_content["changed"],
                        [
                            {"cell": "B2", "value": 42},
                            {"cell": "C2", "value": "=B2*2"},
                        ],
                    )

        asyncio.run(exercise())

    def test_server_uses_streamable_http(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch.object(server.mcp, "run") as run,
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(server.main([]), 0)

        run.assert_called_once_with(
            "streamable-http",
            host="127.0.0.1",
            port=8766,
            streamable_http_path="/mcp",
        )
        self.assertIn("http://127.0.0.1:8766/mcp", stderr.getvalue())

    def test_vscode_uses_builtin_portable_mode_layout(self) -> None:
        vscode_root = REPOSITORY_ROOT / "vscode"
        portable_data = vscode_root / "data"
        vscode_config = json.loads(
            (portable_data / "user-data" / "User" / "mcp.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            vscode_config["servers"]["office"],
            {"type": "http", "url": "http://127.0.0.1:8766/mcp"},
        )
        self.assertEqual(
            vscode_config["servers"]["gostGsm"]["url"],
            "http://127.0.0.1:8765/mcp",
        )
        prompts = portable_data / "user-data" / "User" / "prompts"
        self.assertTrue((prompts / "document-worker.agent.md").is_file())
        self.assertTrue((prompts / "gsm-gost-orchestrator.agent.md").is_file())
        self.assertTrue((portable_data / "tmp").is_dir())
        self.assertFalse((vscode_root / "start.bat").exists())


if __name__ == "__main__":
    unittest.main()
