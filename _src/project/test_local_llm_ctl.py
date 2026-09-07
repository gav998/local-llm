#!/usr/bin/env python3
"""Unit tests for configuration and safety-critical controller decisions."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_llm_ctl import (
    CONTROL_VERSION,
    ControlError,
    Controller,
    tree_fingerprint,
    validate_tree_seal,
)


REQUIRED_FILES = (
    "runtime/python-rag/python.exe",
    "runtime/python-ocr/python.exe",
    "ragflow/api/ragflow_server.py",
    "ragflow/rag/svr/task_executor.py",
    "ragflow/conf/service_conf.yaml",
    "web/index.html",
    "services/mysql/bin/mysqld.exe",
    "services/mysql/bin/mysql.exe",
    "services/mysql/bin/mysqladmin.exe",
    "services/elasticsearch/bin/elasticsearch.bat",
    "services/elasticsearch/config/jvm.options",
    "services/elasticsearch/config/log4j2.properties",
    "services/silo/silo.exe",
    "services/valkey/valkey-server.exe",
    "services/valkey/valkey-cli.exe",
    "services/caddy/caddy.exe",
    "services/ocr/ocr_job_gateway.py",
    "runtime/llama/llama-server.exe",
    "config/project/local_llm_supervisor.py",
)


def make_payload(root: Path) -> None:
    for relative in REQUIRED_FILES:
        path = root / "app" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder\n", encoding="utf-8")


class ControllerConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        make_payload(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_configure_generates_only_app_local_paths_and_persistent_secrets(
        self,
    ) -> None:
        controller = Controller(self.root)
        controller.ensure_config()
        first_secrets = controller.secrets_path.read_bytes()
        controller.ensure_config()

        self.assertEqual(first_secrets, controller.secrets_path.read_bytes())
        location = (controller.config_dir / "location.json").read_text(encoding="utf-8")
        self.assertIn(CONTROL_VERSION, location)
        for generated in (
            controller.config_dir / "mysql.ini",
            controller.config_dir / "valkey.conf",
            controller.config_dir / "Caddyfile",
            controller.app / "ragflow" / "conf" / "local.service_conf.yaml",
        ):
            text = generated.read_text(encoding="utf-8")
            self.assertNotIn(str(Path.home()), text)
        self.assertIn(
            str(self.root).replace("\\", "/"),
            (controller.config_dir / "mysql.ini").read_text(encoding="utf-8"),
        )

    def test_duplicate_ports_fail_closed(self) -> None:
        controller = Controller(self.root)
        controller.ensure_config()
        value = controller.ini_path.read_text(encoding="utf-8")
        controller.ini_path.write_text(
            value.replace("chat = 6381", "chat = 6380"), encoding="utf-8"
        )
        with self.assertRaisesRegex(ControlError, "Ports must be unique"):
            Controller(self.root).ensure_config()

    def test_service_profiles_use_expected_gpu_placement(self) -> None:
        controller = Controller(self.root)
        controller.ensure_config()
        ingestion = controller.services("ingestion")
        chat = controller.services("chat")
        ingestion_command = ingestion["embedding"].command
        chat_embedding_command = chat["embedding"].command
        chat_command = chat["chat"].command

        self.assertEqual(
            ingestion_command[ingestion_command.index("--main-gpu") + 1], "1"
        )
        self.assertEqual(
            chat_embedding_command[chat_embedding_command.index("--main-gpu") + 1], "0"
        )
        self.assertEqual(
            chat_command[chat_command.index("--tensor-split") + 1], "0.20,0.80"
        )
        self.assertEqual(
            ingestion["ocr"].environment["PADDLE_PDX_DISABLE_DEVICE_FALLBACK"], "1"
        )
        self.assertEqual(ingestion["ragflow-api"].environment["HF_HUB_OFFLINE"], "1")

    def test_process_identity_rejects_wrong_identity(self) -> None:
        identity = Controller.process_identity(os.getpid())
        self.assertTrue(identity)
        self.assertTrue(Controller.process_alive(os.getpid(), identity))
        self.assertFalse(Controller.process_alive(os.getpid(), identity + "-wrong"))

    def test_operation_lock_rejects_live_owner_and_recovers_stale_owner(self) -> None:
        controller = Controller(self.root)
        with controller.operation_lock():
            with self.assertRaisesRegex(ControlError, "Another control operation"):
                with controller.operation_lock():
                    pass
        lock = controller.control_dir / "operation.lock"
        lock.mkdir()
        (lock / "owner.json").write_text(
            '{"pid": 999999999, "identity": "stale"}\n', encoding="utf-8"
        )
        with controller.operation_lock():
            self.assertTrue(lock.is_dir())
        self.assertFalse(lock.exists())

    def test_models_must_remain_inside_portable_app(self) -> None:
        controller = Controller(self.root)
        controller.ensure_config()
        value = controller.ini_path.read_text(encoding="utf-8")
        controller.ini_path.write_text(
            value.replace(
                "models/embed/Qwen3-Embedding-8B-Q4_K_M.gguf",
                str(self.root.parent / "outside.gguf"),
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ControlError, "portable app directory"):
            controller.ensure_config()

    def test_process_state_reports_orphan_by_exact_identity(self) -> None:
        controller = Controller(self.root)
        controller.control_dir.mkdir(parents=True)
        metadata = controller.metadata_path("mysql")
        metadata.write_text(
            '{"state":"running","supervisor_pid":10,'
            '"supervisor_identity":"s","child_pid":20,'
            '"child_identity":"c"}\n',
            encoding="utf-8",
        )
        with patch.object(
            Controller,
            "process_alive",
            side_effect=lambda pid, identity="": pid == 20 and identity == "c",
        ):
            state, _ = controller.service_process_state("mysql")
        self.assertEqual(state, "orphaned")

    def test_tree_seal_supports_exact_and_directory_exclusions(self) -> None:
        tree = self.root / "sealed"
        (tree / "logs").mkdir(parents=True)
        (tree / "payload.bin").write_bytes(b"payload")
        (tree / "mutable.conf").write_text("first", encoding="utf-8")
        (tree / "logs" / "runtime.log").write_text("first", encoding="utf-8")
        digest, count = tree_fingerprint(tree, ("mutable.conf", "logs/"))
        marker = self.root / "seal.ok"
        marker.write_text(
            f"fingerprint=test\ntree_sha256={digest}\ntree_file_count={count}\n",
            encoding="utf-8",
        )

        (tree / "mutable.conf").write_text("changed", encoding="utf-8")
        (tree / "logs" / "runtime.log").write_text("changed", encoding="utf-8")
        validate_tree_seal(tree, marker, ("mutable.conf", "logs/"), "test")
        (tree / "payload.bin").write_bytes(b"tampered")
        with self.assertRaisesRegex(ControlError, "no longer matches"):
            validate_tree_seal(tree, marker, ("mutable.conf", "logs/"), "test")


if __name__ == "__main__":
    unittest.main()
