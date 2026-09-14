import importlib.util
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


if __name__ == "__main__":
    unittest.main()
