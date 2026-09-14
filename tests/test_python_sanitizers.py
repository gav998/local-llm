import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).parents[1]


def load_sanitizer(module_name: str):
    path = ROOT / "modules" / module_name / "src" / "sanitize_python_runtime.py"
    spec = importlib.util.spec_from_file_location(f"{module_name}_sanitizer", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PythonRuntimeSanitizerTests(unittest.TestCase):
    def test_removes_absolute_path_launchers_and_updates_record(self) -> None:
        for module_name in ("paddleocr", "ragflow"):
            with self.subTest(module=module_name), tempfile.TemporaryDirectory() as tmp:
                sanitizer = load_sanitizer(module_name)
                runtime = Path(tmp) / "python"
                site_packages = runtime / "Lib" / "site-packages"
                dist_info = site_packages / "demo-1.0.dist-info"
                scripts = runtime / "Scripts"
                dist_info.mkdir(parents=True)
                scripts.mkdir()
                launcher = scripts / "demo.exe"
                launcher.write_bytes(b"embedded C:\\online-build\\python.exe")
                direct_url = dist_info / "direct_url.json"
                direct_url.write_text(
                    '{"url":"file:///C:/online-build/demo.whl"}', encoding="utf-8"
                )
                record = dist_info / "RECORD"
                with record.open("w", encoding="utf-8", newline="") as stream:
                    csv.writer(stream, lineterminator="\n").writerows(
                        [
                            ["../../Scripts/demo.exe", "sha256=ignored", "1"],
                            ["demo-1.0.dist-info/direct_url.json", "", ""],
                            ["demo/__init__.py", "", ""],
                        ]
                    )

                direct_urls, launchers = sanitizer.sanitize(runtime)

                self.assertEqual(direct_urls, ["demo-1.0.dist-info/direct_url.json"])
                self.assertEqual(launchers, ["Scripts/demo.exe"])
                self.assertFalse(launcher.exists())
                self.assertFalse(direct_url.exists())
                rows = list(csv.reader(record.read_text(encoding="utf-8").splitlines()))
                self.assertEqual(rows, [["demo/__init__.py", "", ""]])

    def test_rejects_an_unowned_console_launcher(self) -> None:
        for module_name in ("paddleocr", "ragflow"):
            with self.subTest(module=module_name), tempfile.TemporaryDirectory() as tmp:
                sanitizer = load_sanitizer(module_name)
                runtime = Path(tmp) / "python"
                (runtime / "Lib" / "site-packages").mkdir(parents=True)
                scripts = runtime / "Scripts"
                scripts.mkdir()
                launcher = scripts / "orphan.exe"
                launcher.write_bytes(b"embedded absolute path")

                with self.assertRaisesRegex(RuntimeError, "no owning distribution RECORD"):
                    sanitizer.sanitize(runtime)
                self.assertTrue(launcher.exists())


if __name__ == "__main__":
    unittest.main()
