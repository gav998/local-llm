#!/usr/bin/env python3
"""Unit tests for semantic, hash-free RAGFlow Windows patches."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prepare_ragflow_windows import (
    MOODLE_DIST_INFO,
    MOODLE_REQUIREMENT_ORIGINAL,
    MOODLE_REQUIREMENT_PATCHED,
    TASK_HANDLER_RELATIVE,
    TENANT_LLM_SERVICE_RELATIVE,
    TIKA_COMMAND_ORIGINAL,
    TIKA_COMMAND_PATCHED,
    TIKA_IMPORT_ORIGINAL,
    TIKA_IMPORT_PATCHED,
    TIKA_PROBE_ORIGINAL,
    TIKA_PROBE_PATCHED,
    TIKA_SIGNATURE_PATCHED,
    TIKA_SOURCE_ENTRY,
    clear_record_entry,
    patch_local_embedding_context_limit,
    patch_moodle_metadata,
    patch_task_handler,
    patch_tika_windows_java_launch,
)


class FakeDistribution:
    def __init__(self, dist_info: Path, version: str) -> None:
        self._path = dist_info
        self.version = version


class SemanticSourcePatchTest(unittest.TestCase):
    def test_patches_modified_task_handler_and_is_idempotent(self) -> None:
        source = b"".join(
            [
                b"# local customization\n",
                b"from rag.graphrag.general.index import run_graphrag_for_kb\n",
                b"\nclass Handler:\n",
                b"    async def _run_graphrag(self, embedding_model: LLMBundle) -> None:\n",
                b'        """Run GraphRAG."""\n',
                b"        return await run_graphrag_for_kb()\n",
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / TASK_HANDLER_RELATIVE
            target.parent.mkdir(parents=True)
            target.write_bytes(source)
            patch_task_handler(root)
            patch_task_handler(root)
            result = target.read_bytes()
        self.assertIn(b"# local customization", result)
        self.assertIn(b"        from rag.graphrag.general.index", result)
        self.assertNotIn(b"from rag.graphrag.general.index", result.split(b"class Handler")[0])

    def test_patches_modified_tenant_service_and_is_idempotent(self) -> None:
        source = b"".join(
            [
                b"# retained local customization\n",
                b"class LLM4Tenant:\n",
                b"    def __init__(self, model_config):\n",
                b'        self.max_length = model_config.get("max_tokens") or 8192\n\n',
                b"        self.ready = True\n",
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / TENANT_LLM_SERVICE_RELATIVE
            target.parent.mkdir(parents=True)
            target.write_bytes(source)
            patch_local_embedding_context_limit(root)
            patch_local_embedding_context_limit(root)
            result = target.read_bytes()
        self.assertIn(b"retained local customization", result)
        self.assertEqual(result.count(b"LOCAL_RAGFLOW_EMBEDDING_MAX_TOKENS"), 2)


class MetadataPatchTest(unittest.TestCase):
    def test_clears_record_digest_without_calculating_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "RECORD"
            record.write_text("demo/METADATA,legacy-digest,123\ndemo/RECORD,,\n")
            clear_record_entry(record, "demo/METADATA")
            self.assertEqual(
                record.read_text(), "demo/METADATA,,\ndemo/RECORD,,\n"
            )

    def test_repairs_modified_moodle_metadata_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dist_info = Path(temporary) / MOODLE_DIST_INFO
            dist_info.mkdir()
            metadata = dist_info / "METADATA"
            metadata.write_bytes(
                b"X-Local: keep\n" + MOODLE_REQUIREMENT_ORIGINAL + b"Other: value\n"
            )
            record = dist_info / "RECORD"
            record.write_text(
                f"{MOODLE_DIST_INFO}/METADATA,old-value,999\n"
                f"{MOODLE_DIST_INFO}/RECORD,,\n"
            )
            fake = FakeDistribution(dist_info, "0.24.1")
            with mock.patch("importlib.metadata.distribution", return_value=fake):
                patch_moodle_metadata()
                patch_moodle_metadata()
            result = metadata.read_bytes()
            self.assertIn(b"X-Local: keep", result)
            self.assertIn(MOODLE_REQUIREMENT_PATCHED, result)
            self.assertNotIn(MOODLE_REQUIREMENT_ORIGINAL, result)
            self.assertTrue(record.read_text().startswith(f"{MOODLE_DIST_INFO}/METADATA,,"))


class TikaPatchTest(unittest.TestCase):
    def test_patches_modified_tika_source_and_clears_record_digest(self) -> None:
        source_data = b"".join(
            [
                b"# local tweak\n",
                TIKA_IMPORT_ORIGINAL,
                b"def checkJarSig(tikaServerJar, jarPath):\n",
                b"    # locally modified legacy verifier\n",
                b"    return jarPath.endswith('.jar')\n",
                b"\n",
                b"def startServer():\n",
                TIKA_COMMAND_ORIGINAL,
                b"    try:\n",
                TIKA_PROBE_ORIGINAL,
                b"    except FileNotFoundError:\n        return False\n",
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            site_packages = Path(temporary)
            dist_info = site_packages / "tika-2.6.0.dist-info"
            dist_info.mkdir()
            source = site_packages / TIKA_SOURCE_ENTRY
            source.parent.mkdir()
            source.write_bytes(source_data)
            record = dist_info / "RECORD"
            record.write_text(f"{TIKA_SOURCE_ENTRY},old-value,123\ntika-2.6.0.dist-info/RECORD,,\n")
            fake = FakeDistribution(dist_info, "2.6.0")
            with mock.patch("importlib.metadata.distribution", return_value=fake):
                patch_tika_windows_java_launch()
                patch_tika_windows_java_launch()
            result = source.read_bytes()
            self.assertIn(b"# local tweak", result)
            self.assertIn(TIKA_IMPORT_PATCHED, result)
            self.assertIn(TIKA_COMMAND_PATCHED, result)
            self.assertIn(TIKA_PROBE_PATCHED, result)
            self.assertIn(TIKA_SIGNATURE_PATCHED, result)
            self.assertTrue(record.read_text().startswith(f"{TIKA_SOURCE_ENTRY},,"))


if __name__ == "__main__":
    unittest.main()
