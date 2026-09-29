from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from mcp import Client

import server


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

    def test_workspace_mcp_configuration_targets_portable_launcher(self) -> None:
        config = json.loads(
            (REPOSITORY_ROOT / ".vscode" / "mcp.json").read_text(encoding="utf-8")
        )
        definition = config["servers"]["gostGsm"]
        self.assertEqual(definition["type"], "stdio")
        self.assertIn("gost-gsm-mcp", definition["command"])
        self.assertIn("GOST_GSM_PROJECT_ROOT", definition["env"])

    def test_launcher_keeps_runtime_data_local(self) -> None:
        script = (REPOSITORY_ROOT / "gost-gsm-mcp" / "run.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("$env:APPDATA = Join-Path $PortableProfile", script)
        self.assertIn("$env:TEMP = $PortableTemp", script)
        self.assertNotIn("setx", script.casefold())
        self.assertNotIn("docker", script.casefold())


if __name__ == "__main__":
    unittest.main()
