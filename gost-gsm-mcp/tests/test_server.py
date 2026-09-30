from __future__ import annotations

import asyncio
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server
from mcp import Client

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class McpServerTests(unittest.TestCase):
    def test_all_expected_tools_register_and_are_callable(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as directory:
                async with Client(server.mcp) as client:
                    listing = await client.list_tools()
                    names = {tool.name for tool in listing.tools}
                    self.assertEqual(
                        names,
                        {
                            "scan_source",
                            "initialize_project",
                            "get_progress",
                            "get_next_batch",
                            "process_next_batch",
                            "process_batches",
                            "list_review_flags",
                            "get_row_detail",
                            "find_purpose_candidates",
                            "set_item_override",
                            "set_purpose_override",
                            "add_relation_override",
                            "clear_override",
                            "validate_final",
                            "export_final_csv",
                        },
                    )
                    response = await client.call_tool(
                        "get_progress", {"project_root": str(Path(directory) / "job")}
                    )
                    self.assertFalse(response.is_error)
                    self.assertEqual(
                        response.structured_content["status"], "not_initialized"
                    )

        asyncio.run(exercise())

    def test_module_owns_its_http_configuration(self) -> None:
        config = server.server_config()
        self.assertEqual(config["host"], "127.0.0.1")
        self.assertEqual(config["port"], 8765)
        self.assertEqual(config["path"], "/mcp")

    def test_launcher_keeps_runtime_data_local(self) -> None:
        script = (REPOSITORY_ROOT / "gost-gsm-mcp" / "run.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("$env:APPDATA = Join-Path $PortableProfile", script)
        self.assertIn("$env:TEMP = $PortableTemp", script)
        self.assertNotIn("setx", script.casefold())
        self.assertNotIn("docker", script.casefold())

    def test_launcher_uses_windows_powershell_compatible_redirection(self) -> None:
        script = (REPOSITORY_ROOT / "gost-gsm-mcp" / "run.ps1").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("1>&2", script)
        self.assertEqual(
            script.count("ForEach-Object { [Console]::Error.WriteLine($_) }"), 2
        )

    def test_server_reports_http_endpoint_to_stderr(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch.object(server.mcp, "run") as run,
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(server.main([]), 0)

        run.assert_called_once_with(
            "streamable-http",
            host="127.0.0.1",
            port=8765,
            streamable_http_path="/mcp",
        )
        message = stderr.getvalue()
        self.assertIn("http://127.0.0.1:8765/mcp", message)
        self.assertIn("http://127.0.0.1:8765/health", message)
        self.assertIn("Job directory:", message)

    def test_launcher_reports_startup_status_to_stderr(self) -> None:
        script = (REPOSITORY_ROOT / "gost-gsm-mcp" / "run.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("Launcher started. Checking the portable runtime", script)
        self.assertIn("Starting the HTTP server. Job directory:", script)
        self.assertIn('[Console]::Error.WriteLine("[GOST GSM MCP] $Message")', script)


if __name__ == "__main__":
    unittest.main()
