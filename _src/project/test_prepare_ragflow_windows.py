#!/usr/bin/env python3
"""Unit tests for RAGFlow Windows preparation safety checks."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from prepare_ragflow_windows import (
    DATRIE_WHEEL_NAME,
    DATRIE_WHEEL_SHA256,
    validate_datrie_direct_url,
)


def direct_url_payload(archive_info: dict[str, object]) -> bytes:
    return (
        json.dumps(
            {
                "archive_info": archive_info,
                "url": f"file:///I:/local-llm-main/_src/{DATRIE_WHEEL_NAME}",
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

    def test_rejects_wrong_datrie_hash(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Unexpected or missing"):
            validate_datrie_direct_url(
                direct_url_payload({"hash": "sha256:" + "0" * 64}),
                Path("direct_url.json"),
            )


if __name__ == "__main__":
    unittest.main()
