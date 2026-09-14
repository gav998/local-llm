#!/usr/bin/env python3
"""Unit contracts for Python runtime path sanitization."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import sanitize_python_runtime


class RemoveConsoleLaunchersTests(unittest.TestCase):
    def test_removes_owned_exe_and_text_launchers_but_keeps_unowned_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "python"
            scripts = runtime / "Scripts"
            site_packages = runtime / "Lib" / "site-packages"
            record = site_packages / "example-1.0.dist-info" / "RECORD"
            record.parent.mkdir(parents=True)
            scripts.mkdir(parents=True)

            exe = scripts / "example.exe"
            text = scripts / "example-tool"
            python_script = scripts / "example_tool.py"
            unowned = scripts / "activate"
            exe.write_bytes(b"MZ launcher with an absolute interpreter")
            text.write_text("#!C:/build/python.exe\n", encoding="utf-8")
            python_script.write_text("#!C:/build/python.exe\n", encoding="utf-8")
            unowned.write_text("portable activation helper\n", encoding="utf-8")

            targets = [
                "../../Scripts/example.exe",
                "../../Scripts/example-tool",
                "../../Scripts/example_tool.py",
                "example/__init__.py",
            ]
            with record.open("w", encoding="utf-8", newline="") as stream:
                csv.writer(stream, lineterminator="\n").writerows(
                    [[target, "", ""] for target in targets]
                )

            removed = sanitize_python_runtime.remove_console_launchers(
                runtime, site_packages
            )

            self.assertEqual(
                removed,
                [
                    "Scripts/example-tool",
                    "Scripts/example.exe",
                    "Scripts/example_tool.py",
                ],
            )
            self.assertFalse(exe.exists())
            self.assertFalse(text.exists())
            self.assertFalse(python_script.exists())
            self.assertTrue(unowned.is_file())
            with record.open("r", encoding="utf-8", newline="") as stream:
                self.assertEqual(
                    list(csv.reader(stream)), [["example/__init__.py", "", ""]]
                )

    def test_rejects_unowned_exe_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "python"
            scripts = runtime / "Scripts"
            site_packages = runtime / "Lib" / "site-packages"
            record = site_packages / "example-1.0.dist-info" / "RECORD"
            record.parent.mkdir(parents=True)
            scripts.mkdir(parents=True)
            record.write_text("example/__init__.py,,\n", encoding="utf-8")
            (scripts / "orphan.exe").write_bytes(b"MZ")

            with self.assertRaisesRegex(
                RuntimeError, "Console launcher has no owning distribution RECORD"
            ):
                sanitize_python_runtime.remove_console_launchers(
                    runtime, site_packages
                )


class RemoveNonRuntimeTreeTests(unittest.TestCase):
    def test_removes_litellm_benchmarks_and_their_record_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site_packages = Path(temporary) / "site-packages"
            benchmarks = (
                site_packages / sanitize_python_runtime.LITELLM_GUARDRAIL_BENCHMARKS
            )
            result = benchmarks / "results" / (
                "block_age_discrimination_-_contentfilter_"
                "(age_discrimination.yaml).json"
            )
            sibling = benchmarks.parent / "content_filter_handler.py"
            record = site_packages / "litellm-1.0.dist-info" / "RECORD"
            result.parent.mkdir(parents=True)
            sibling.parent.mkdir(parents=True, exist_ok=True)
            record.parent.mkdir(parents=True)
            result.write_text("{}\n", encoding="utf-8")
            sibling.write_text("# runtime code\n", encoding="utf-8")
            with record.open("w", encoding="utf-8", newline="") as stream:
                csv.writer(stream, lineterminator="\n").writerows(
                    [
                        [
                            result.relative_to(site_packages).as_posix(),
                            "",
                            "",
                        ],
                        [sibling.relative_to(site_packages).as_posix(), "", ""],
                    ]
                )

            removed = sanitize_python_runtime.remove_non_runtime_tree(
                site_packages,
                sanitize_python_runtime.LITELLM_GUARDRAIL_BENCHMARKS,
                "litellm-*.dist-info/RECORD",
            )

            self.assertEqual(
                removed,
                {
                    "path": sanitize_python_runtime.LITELLM_GUARDRAIL_BENCHMARKS.as_posix(),
                    "files_removed": 1,
                },
            )
            self.assertFalse(benchmarks.exists())
            self.assertTrue(sibling.is_file())
            with record.open("r", encoding="utf-8", newline="") as stream:
                self.assertEqual(
                    list(csv.reader(stream)),
                    [[sibling.relative_to(site_packages).as_posix(), "", ""]],
                )


if __name__ == "__main__":
    unittest.main()
