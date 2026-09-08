#!/usr/bin/env python3
"""Unit tests for RAGFlow Windows preparation safety checks."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prepare_ragflow_windows import (
    DATRIE_WHEEL_NAME,
    DATRIE_WHEEL_SHA256,
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


if __name__ == "__main__":
    unittest.main()
