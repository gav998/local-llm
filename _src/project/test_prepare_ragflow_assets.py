#!/usr/bin/env python3
"""Unit tests for pinned RAGFlow asset preparation."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prepare_ragflow_assets import Asset, download_asset, ensure_assets


class FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._data):
            return b""
        if size < 0:
            size = len(self._data) - self._offset
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class DownloadAssetTest(unittest.TestCase):
    def test_retries_timeout_and_installs_verified_asset(self) -> None:
        payload = b"verified payload"
        asset = Asset(
            "asset.bin",
            "https://example.invalid/asset.bin",
            len(payload),
            hashlib.sha256(payload).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "asset.bin"
            with mock.patch(
                "urllib.request.urlopen",
                side_effect=[TimeoutError("read timed out"), FakeResponse(payload)],
            ) as urlopen, mock.patch("time.sleep"):
                download_asset(asset, destination)

            self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual(urlopen.call_count, 2)
            self.assertEqual(list(destination.parent.glob("*.part")), [])

    def test_installs_verified_manual_asset_before_network(self) -> None:
        payload = b"manual payload"
        asset = Asset(
            "nested/asset.bin",
            "https://example.invalid/asset.bin",
            len(payload),
            hashlib.sha256(payload).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            manual = Path(temporary) / "manual"
            manual.mkdir()
            (manual / "asset.bin").write_bytes(payload)

            with mock.patch("urllib.request.urlopen") as urlopen:
                ensure_assets(
                    root, [asset], verify_only=False, manual_assets_dir=manual
                )

            self.assertEqual((root / "nested" / "asset.bin").read_bytes(), payload)
            urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
