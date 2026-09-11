#!/usr/bin/env python3
"""Unit tests for RAGFlow Windows preparation safety checks."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import PurePosixPath
from pathlib import Path
from unittest import mock

from prepare_ragflow_windows import (
    DATRIE_WHEEL_NAME,
    DATRIE_WHEEL_SHA256,
    MOODLE_DIST_INFO,
    MOODLE_REQUIREMENT_ORIGINAL,
    MOODLE_REQUIREMENT_PATCHED,
    patch_moodle_metadata,
    record_digest,
    validate_datrie_direct_url,
)


def direct_url_payload(archive_info: dict[str, object], url: str | None = None) -> bytes:
    return (
        json.dumps(
            {
                "archive_info": archive_info,
                "url": url or f"file:///I:/local-llm-main/_src/{DATRIE_WHEEL_NAME}",
            },
            sort_keys=True,
        ).encode("utf-8")
    )


class DatrieDirectUrlTest(unittest.TestCase):
    def test_accepts_pep_610_sha256_equals_hash(self) -> None:
        validate_datrie_direct_url(
            direct_url_payload({"hash": f"sha256={DATRIE_WHEEL_SHA256}"}),
            Path("direct_url.json"),
        )

    def test_accepts_uv_sha256_colon_hash(self) -> None:
        validate_datrie_direct_url(
            direct_url_payload({"hash": f"sha256:{DATRIE_WHEEL_SHA256}"}),
            Path("direct_url.json"),
        )

    def test_accepts_hashes_sha256_hash(self) -> None:
        validate_datrie_direct_url(
            direct_url_payload({"hashes": {"sha256": DATRIE_WHEEL_SHA256}}),
            Path("direct_url.json"),
        )

    def test_accepts_missing_hash_when_local_wheel_matches(self) -> None:
        wheel_data = b"local datrie wheel"
        with tempfile.TemporaryDirectory() as temporary:
            wheel = Path(temporary) / DATRIE_WHEEL_NAME
            wheel.write_bytes(wheel_data)
            with mock.patch(
                "prepare_ragflow_windows.DATRIE_WHEEL_SHA256",
                hashlib.sha256(wheel_data).hexdigest(),
            ):
                validate_datrie_direct_url(
                    direct_url_payload({}, wheel.as_uri()),
                    Path("direct_url.json"),
                )

    def test_rejects_missing_hash_when_local_wheel_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wheel = Path(temporary) / DATRIE_WHEEL_NAME
            with self.assertRaisesRegex(RuntimeError, "local wheel is not available"):
                validate_datrie_direct_url(
                    direct_url_payload({}, wheel.as_uri()),
                    Path("direct_url.json"),
                )

    def test_rejects_wrong_datrie_hash(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Unexpected datrie wheel hash"):
            validate_datrie_direct_url(
                direct_url_payload({"hash": "sha256:" + "0" * 64}),
                Path("direct_url.json"),
            )


class FakeDistribution:
    version = "0.24.1"
    files = [
        PurePosixPath(f"{MOODLE_DIST_INFO}/METADATA"),
        PurePosixPath(f"{MOODLE_DIST_INFO}/RECORD"),
    ]

    def __init__(self, site_packages: Path) -> None:
        self.site_packages = site_packages

    def locate_file(self, path: PurePosixPath) -> Path:
        return self.site_packages / path.as_posix()


def synthetic_moodle_metadata() -> tuple[bytes, bytes]:
    original = b"".join(
        [
            b"Metadata-Version: 2.1\n",
            b"Name: moodlepy\n",
            b"Version: 0.24.1\n",
            MOODLE_REQUIREMENT_ORIGINAL,
            b"Requires-Dist: cattrs (>=22.2.0,<23.0.0)\n",
        ]
    )
    patched = original.replace(MOODLE_REQUIREMENT_ORIGINAL, MOODLE_REQUIREMENT_PATCHED)
    return original, patched


class MoodleMetadataRepairTest(unittest.TestCase):
    def test_repairs_moodle_attrs_requirement_and_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site_packages = Path(temporary)
            dist_info = site_packages / MOODLE_DIST_INFO
            dist_info.mkdir()
            metadata = dist_info / "METADATA"
            record = dist_info / "RECORD"
            original, patched = synthetic_moodle_metadata()
            original_sha = hashlib.sha256(original).hexdigest()
            patched_sha = hashlib.sha256(patched).hexdigest()
            metadata.write_bytes(original)
            record.write_text(
                "\n".join(
                    [
                        (
                            f"{MOODLE_DIST_INFO}/METADATA,"
                            f"sha256={record_digest(original_sha)},"
                            f"{len(original)}"
                        ),
                        f"{MOODLE_DIST_INFO}/RECORD,,",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch.multiple(
                "prepare_ragflow_windows",
                MOODLE_METADATA_ORIGINAL_SIZE=len(original),
                MOODLE_METADATA_ORIGINAL_SHA256=original_sha,
                MOODLE_METADATA_PATCHED_SIZE=len(patched),
                MOODLE_METADATA_PATCHED_SHA256=patched_sha,
            ), mock.patch(
                "importlib.metadata.distribution",
                return_value=FakeDistribution(site_packages),
            ):
                patch_moodle_metadata()
                patch_moodle_metadata()

            repaired = metadata.read_bytes()
            self.assertEqual(repaired, patched)
            self.assertNotIn(MOODLE_REQUIREMENT_ORIGINAL, repaired)
            self.assertIn(MOODLE_REQUIREMENT_PATCHED, repaired)
            rows = record.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                rows[0],
                (
                    f"{MOODLE_DIST_INFO}/METADATA,"
                    f"sha256={record_digest(patched_sha)},"
                    f"{len(patched)}"
                ),
            )


if __name__ == "__main__":
    unittest.main()
