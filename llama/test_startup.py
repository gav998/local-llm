from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parent


class LlamaAgentStartupTests(unittest.TestCase):
    def test_default_config_is_agent_ready(self) -> None:
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

        self.assertTrue(config["vscode_tool_calling"])
        self.assertGreaterEqual(config["llm_context"], 16_384)
        self.assertEqual(config["llm_long_context_tensor_split"], "0.50,0.50")
        self.assertGreaterEqual(
            config["llm_context"] - config["vscode_max_output_tokens"], 12_000
        )

    def test_startup_enables_and_validates_tools(self) -> None:
        script = (ROOT / "start.ps1").read_text(encoding="utf-8")

        self.assertIn("$Arguments += '--jinja'", script)
        self.assertIn("/props", script)
        self.assertIn("supports_tools", script)
        self.assertIn("Get-ActualContext", script)
        self.assertNotIn("Configure VS Code automatically", script)
        self.assertNotIn("Install-VsCodeConfiguration", script)

    def test_context_is_selected_at_startup_and_written_to_vscode_model(self) -> None:
        script = (ROOT / "start.ps1").read_text(encoding="utf-8")

        for context in ("16384", "32768", "65536", "131072"):
            self.assertIn(context, script)
        self.assertIn("Exact context tokens", script)
        self.assertIn("-Context $ChatContext", script)
        self.assertIn("maxInputTokens = $Context - $VsCodeMaxOutputTokens", script)
        self.assertIn("ConvertTo-Json -InputObject $VsCodeProviders", script)

    def test_long_context_uses_memory_saving_and_qwen_yarn_options(self) -> None:
        script = (ROOT / "start.ps1").read_text(encoding="utf-8")

        self.assertIn("$CacheType = 'q8_0'", script)
        self.assertIn("--cache-type-k", script)
        self.assertIn("--cache-type-v", script)
        self.assertIn("--flash-attn", script)
        self.assertIn("--rope-scaling','yarn'", script)
        self.assertIn("--yarn-orig-ctx','32768'", script)
        self.assertIn("--fit','off'", script)


if __name__ == "__main__":
    unittest.main()
