from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parent


class LlamaAgentStartupTests(unittest.TestCase):
    def test_default_profile_is_agent_ready(self) -> None:
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

        self.assertTrue(config["vscode_tool_calling"])
        self.assertGreaterEqual(config["llm_context"], 16_384)
        self.assertGreaterEqual(
            config["llm_context"] - config["vscode_max_output_tokens"], 12_000
        )

    def test_startup_enables_and_validates_tools(self) -> None:
        script = (ROOT / "start.ps1").read_text(encoding="utf-8")

        self.assertIn("$Arguments += '--jinja'", script)
        self.assertIn("/props", script)
        self.assertIn("supports_tools", script)
        self.assertIn("Configure VS Code automatically", script)
        self.assertIn("chatLanguageModels.json", script)
        self.assertIn("Where-Object", script)
        self.assertIn(".backup-", script)


if __name__ == "__main__":
    unittest.main()
