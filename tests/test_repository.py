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

    def test_python_import_probes_survive_windows_powershell_quoting(self) -> None:
        modules = Path(__file__).parents[1] / "modules"
        for name in ("paddleocr", "ragflow"):
            with self.subTest(module=name):
                control = (modules / name / "control.ps1").read_text(
                    encoding="utf-8"
                )
                self.assertNotIn('print("', control)
                self.assertIn("& $Python -c 'import ", control)

    def test_ragflow_archives_exclude_upstream_development_symlinks(self) -> None:
        root = Path(__file__).parents[1]
        expected = [
            "ragflow-0.27.1/CLAUDE.md",
            "ragflow-0.27.1/internal/deepdoc/parser/pdf/tool/testdata",
        ]
        for module_name in ("ragflow", "web"):
            with self.subTest(module=module_name):
                module_root = root / "modules" / module_name
                manifest = json.loads(
                    (module_root / "module.json").read_text(encoding="utf-8")
                )
                source = next(
                    artifact
                    for artifact in manifest["artifacts"]
                    if artifact["file"] == "ragflow-0.27.1.zip"
                )
                self.assertEqual(source["extract_excludes"], expected)
                prepare = (module_root / "src" / "prepare.ps1").read_text(
                    encoding="utf-8"
                )
                self.assertIn("[string[]]$ExcludeEntries", prepare)
                self.assertIn('"-x!$Entry"', prepare)

    def test_ragflow_wheel_build_avoids_prefixed_bytecode_paths(self) -> None:
        hook = (
            Path(__file__).parents[1] / "modules" / "ragflow" / "src" / "build-hook.ps1"
        ).read_text(encoding="utf-8")
        wheel_command = "@('-m','pip','wheel'"
        self.assertIn("$PreviousPythonPycCachePrefix=$env:PYTHONPYCACHEPREFIX", hook)
        self.assertLess(
            hook.index("Remove-Item Env:PYTHONPYCACHEPREFIX"),
            hook.index(wheel_command),
        )
        self.assertIn("$env:PYTHONPYCACHEPREFIX=$PreviousPythonPycCachePrefix", hook)

    def test_module_packaging_uses_short_windows_staging_paths(self) -> None:
        modules = Path(__file__).parents[1] / "modules"
        for prepare_path in sorted(modules.glob("*/src/prepare.ps1")):
            with self.subTest(module=prepare_path.parents[1].name):
                prepare = prepare_path.read_text(encoding="utf-8")
                self.assertIn("$PackageRoot = Join-Path $BuildRoot 'p'", prepare)
                self.assertIn("$Rehydrate=Join-Path $BuildRoot 'r'", prepare)
                self.assertNotIn("Join-Path $BuildRoot 'package'", prepare)
                self.assertNotIn("Join-Path $BuildRoot 'rehydrate'", prepare)

    def test_module_packaging_hashes_literal_file_paths(self) -> None:
        modules = Path(__file__).parents[1] / "modules"
        for prepare_path in sorted(modules.glob("*/src/prepare.ps1")):
            with self.subTest(module=prepare_path.parents[1].name):
                prepare = prepare_path.read_text(encoding="utf-8")
                self.assertIn("Get-FileHash -LiteralPath $File.FullName", prepare)
                self.assertNotIn("Get-FileHash $_.FullName", prepare)
                self.assertIn('throw "Cannot hash packaged file:', prepare)


if __name__ == "__main__":
    unittest.main()
