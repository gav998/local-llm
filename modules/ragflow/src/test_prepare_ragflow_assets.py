#!/usr/bin/env python3
"""Unit tests for hash-free RAGFlow asset preparation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prepare_ragflow_assets import Asset, download_asset, ensure_assets


class FakeResponse:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if self.offset >= len(self.data):
            return b""
        if size < 0:
            size = len(self.data) - self.offset
        chunk = self.data[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class DownloadAssetTest(unittest.TestCase):
    def test_retries_timeout_and_installs_download_without_digest_pass(self) -> None:
        asset = Asset("asset.bin", "https://example.invalid/asset.bin")
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "asset.bin"
            with mock.patch(
                "urllib.request.urlopen",
                side_effect=[TimeoutError("read timed out"), FakeResponse(b"payload")],
            ) as urlopen, mock.patch("time.sleep"):
                download_asset(asset, destination)
            self.assertEqual(destination.read_bytes(), b"payload")
            self.assertEqual(urlopen.call_count, 2)

    def test_existing_modified_asset_is_accepted_without_network(self) -> None:
        asset = Asset("asset.bin", "https://example.invalid/asset.bin")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "asset.bin").write_bytes(b"locally modified")
            with mock.patch("urllib.request.urlopen") as urlopen:
                ensure_assets(root, [asset], verify_only=False)
            urlopen.assert_not_called()

    def test_manual_asset_is_used_before_network(self) -> None:
        asset = Asset("nested/asset.bin", "https://example.invalid/asset.bin")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            manual = Path(temporary) / "manual"
            manual.mkdir()
            (manual / "asset.bin").write_bytes(b"manual payload")
            with mock.patch("urllib.request.urlopen") as urlopen:
                ensure_assets(root, [asset], False, manual)
            self.assertEqual((root / "nested/asset.bin").read_bytes(), b"manual payload")
            urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
