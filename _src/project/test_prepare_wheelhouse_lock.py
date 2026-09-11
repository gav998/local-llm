#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

from prepare_wheelhouse_lock import WheelhouseLockError, create_lock  # noqa: E402


def make_wheel(directory: Path, name: str, version: str) -> Path:
    wheel_name = name.replace("-", "_")
    path = directory / f"{wheel_name}-{version}-py3-none-any.whl"
    metadata_dir = f"{wheel_name}-{version}.dist-info"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{metadata_dir}/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        )
        archive.writestr(f"{metadata_dir}/WHEEL", "Wheel-Version: 1.0\n")
    return path


class PrepareWheelhouseLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.wheelhouse = self.root / "wheelhouse"
        self.wheelhouse.mkdir()
        self.requirements = self.root / "source.txt"
        self.output = self.root / "requirements.lock"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_normalizes_pins_and_direct_wheel_urls(self) -> None:
        make_wheel(self.wheelhouse, "source-only", "1.0.0")
        make_wheel(self.wheelhouse, "ja-core-news-sm", "3.8.0")
        make_wheel(self.wheelhouse, "unrelated-stale", "9.9")
        self.requirements.write_text(
            "source_only==1.0.0 \\\n"
            "    --hash=sha256:" + "a" * 64 + "\n"
            "ja-core-news-sm @ https://example.invalid/ja_core_news_sm-3.8.0-py3-none-any.whl \\\n"
            "    --hash=sha256:" + "b" * 64 + "\n"
            "ignored-on-this-platform==4.0 ; sys_platform == 'never'\n",
            encoding="utf-8",
        )

        count = create_lock(self.requirements, self.wheelhouse, self.output)

        self.assertEqual(count, 2)
        ja_hash = hashlib.sha256(
            (self.wheelhouse / "ja_core_news_sm-3.8.0-py3-none-any.whl").read_bytes()
        ).hexdigest()
        source_hash = hashlib.sha256(
            (self.wheelhouse / "source_only-1.0.0-py3-none-any.whl").read_bytes()
        ).hexdigest()
        self.assertEqual(
            self.output.read_text(encoding="utf-8"),
            f"ja-core-news-sm==3.8.0 \\\n"
            f"    --hash=sha256:{ja_hash}\n"
            f"source-only==1.0.0 \\\n"
            f"    --hash=sha256:{source_hash}\n",
        )

    def test_rejects_missing_locked_wheel(self) -> None:
        make_wheel(self.wheelhouse, "example", "2.0")
        self.requirements.write_text("example==1.0\n", encoding="utf-8")

        with self.assertRaisesRegex(WheelhouseLockError, "example==1.0"):
            create_lock(self.requirements, self.wheelhouse, self.output)

    def test_rejects_direct_source_archive(self) -> None:
        make_wheel(self.wheelhouse, "example", "1.0")
        self.requirements.write_text(
            "example @ https://example.invalid/example-1.0.tar.gz\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(WheelhouseLockError, "not a wheel"):
            create_lock(self.requirements, self.wheelhouse, self.output)

    def test_rejects_ambiguous_wheels_for_one_pin(self) -> None:
        first = make_wheel(self.wheelhouse, "example", "1.0")
        second = self.wheelhouse / "example-1.0-cp313-cp313-win_amd64.whl"
        second.write_bytes(first.read_bytes())
        self.requirements.write_text("example==1.0\n", encoding="utf-8")

        with self.assertRaisesRegex(WheelhouseLockError, "ambiguous wheels"):
            create_lock(self.requirements, self.wheelhouse, self.output)


if __name__ == "__main__":
    unittest.main()
