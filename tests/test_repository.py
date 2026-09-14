import importlib.util
import json
from pathlib import Path
import unittest


class RepositoryContractTests(unittest.TestCase):
    def test_modular_repository_contract(self) -> None:
        script = Path(__file__).parents[1] / "scripts" / "validate_repository.py"
        spec = importlib.util.spec_from_file_location("validate_repository", script)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader if spec else None)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        self.assertEqual(module.validate(), [])

    def test_native_build_logs_do_not_turn_stderr_into_fatal_errors(self) -> None:
        modules = Path(__file__).parents[1] / "modules"
        for name in ("paddleocr", "ragflow", "web"):
            with self.subTest(module=name):
                hook = (modules / name / "src" / "build-hook.ps1").read_text(
                    encoding="utf-8"
                )
                self.assertIn("function Invoke-LoggedNative", hook)
                self.assertIn("$ErrorActionPreference='Continue'", hook)
                self.assertEqual(hook.count("*>>$Log"), 1)

    def test_ragflow_archive_excludes_upstream_development_symlinks(self) -> None:
        root = Path(__file__).parents[1]
        module_root = root / "modules" / "ragflow"
        manifest = json.loads((module_root / "module.json").read_text(encoding="utf-8"))
        source = next(
            artifact
            for artifact in manifest["artifacts"]
            if artifact["file"] == "ragflow-0.27.1.zip"
        )

        self.assertEqual(
            source["extract_excludes"],
            [
                "ragflow-0.27.1/CLAUDE.md",
                "ragflow-0.27.1/internal/deepdoc/parser/pdf/tool/testdata",
            ],
        )
        prepare = (module_root / "src" / "prepare.ps1").read_text(encoding="utf-8")
        self.assertIn("[string[]]$ExcludeEntries", prepare)
        self.assertIn('"-x!$Entry"', prepare)


if __name__ == "__main__":
    unittest.main()
