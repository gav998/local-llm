#!/usr/bin/env python3
"""Unit contracts for the fail-closed portability auditor."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import audit_portability


class AuditPortabilityPathTest(unittest.TestCase):
    def test_windows_drive_path_uses_extended_length_form(self) -> None:
        with (
            mock.patch.object(audit_portability.os, "name", "nt"),
            mock.patch.object(
                audit_portability.os.path,
                "abspath",
                return_value=r"C:\portable\app\runtime\python-rag\long-file.json",
            ),
        ):
            actual = audit_portability._io_path(
                Path("C:/portable/app/runtime/python-rag/long-file.json")
            )
        self.assertEqual(
            actual,
            r"\\?\C:\portable\app\runtime\python-rag\long-file.json",
        )

    def test_windows_unc_path_uses_extended_length_form(self) -> None:
        with (
            mock.patch.object(audit_portability.os, "name", "nt"),
            mock.patch.object(
                audit_portability.os.path,
                "abspath",
                return_value=r"\\server\share\portable\file.json",
            ),
        ):
            actual = audit_portability._io_path(Path("ignored"))
        self.assertEqual(
            actual,
            r"\\?\UNC\server\share\portable\file.json",
        )

    def test_non_windows_path_is_unchanged(self) -> None:
        path = Path(__file__).resolve()
        self.assertEqual(audit_portability._io_path(path), str(path))


if __name__ == "__main__":
    unittest.main()
