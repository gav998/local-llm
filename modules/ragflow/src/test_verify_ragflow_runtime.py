#!/usr/bin/env python3
"""Unit tests for the prepared RAGFlow runtime verifier."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from verify_ragflow_runtime import configure_nltk_data


class PortableNltkConfigurationTest(unittest.TestCase):
    def test_replaces_external_nltk_roots_with_bundled_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "payload"
            ragflow_dir = payload / "ragflow"
            nltk_dir = payload / "assets" / "nltk"
            ragflow_dir.mkdir(parents=True)
            nltk_dir.mkdir(parents=True)

            with mock.patch.dict(
                os.environ, {"NLTK_DATA": "C:\\Users\\someone\\nltk_data"}
            ):
                result = configure_nltk_data(ragflow_dir)

                self.assertEqual(os.environ["NLTK_DATA"], str(nltk_dir))
                self.assertEqual(result, f"NLTK_DATA={nltk_dir}")

    def test_rejects_missing_bundled_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ragflow_dir = Path(temporary) / "payload" / "ragflow"
            ragflow_dir.mkdir(parents=True)

            with self.assertRaisesRegex(
                RuntimeError, "Missing safe portable NLTK data directory"
            ):
                configure_nltk_data(ragflow_dir)


if __name__ == "__main__":
    unittest.main()
