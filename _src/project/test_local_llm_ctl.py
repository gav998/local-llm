#!/usr/bin/env python3
"""Unit tests for configuration and safety-critical controller decisions."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import Mock, patch

from local_llm_ctl import (
    CONTROL_VERSION,
    ControlError,
    Controller,
    cygwin_path,
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

    def test_elasticsearch_runtime_files_are_kept_outside_vendor_tree(self) -> None:
        controller = Controller(self.root)
        controller.ensure_config()

        service = controller.services("core")["elasticsearch"]

        self.assertEqual(
            service.cwd, controller.logs_dir / "elasticsearch" / "runtime"
        )
        self.assertTrue(service.cwd.is_dir())

    def test_valkey_config_uses_cygwin_path(self) -> None:
        self.assertEqual(
            cygwin_path(PureWindowsPath(r"I:\test\app\config\runtime\valkey.conf")),
            "/cygdrive/i/test/app/config/runtime/valkey.conf",
        )

        controller = Controller(self.root)
        controller.ensure_config()
        converted = "/cygdrive/i/test/app/config/runtime/valkey.conf"
        with patch("local_llm_ctl.cygwin_path", return_value=converted) as convert:
            command = controller.services("core")["valkey"].command

        convert.assert_called_once_with(controller.config_dir / "valkey.conf")
        self.assertEqual(command[1], converted)

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

    def test_mysql_health_requires_an_authenticated_query(self) -> None:
        controller = Controller(self.root)
        controller.ensure_config()
        denied = Mock(
            returncode=1,
            stdout="",
            stderr="ERROR 1045 (28000): Access denied",
        )

        with patch("local_llm_ctl.subprocess.run", return_value=denied) as run:
            self.assertFalse(controller._mysql_health())

        command = run.call_args.args[0]
        self.assertTrue(command[0].endswith("mysql.exe"))
        self.assertNotIn("mysqladmin.exe", command[0])
        self.assertEqual(command[-1], "SELECT 1")
        self.assertIn(
            "Access denied",
            (controller.logs_dir / "mysql-client-error.log").read_text(
                encoding="utf-8"
            ),
        )

    def test_mysql_bootstrap_creates_loopback_account_via_init_file(self) -> None:
        controller = Controller(self.root)
        controller.ensure_config()
        (controller.data_dir / "mysql" / "mysql").mkdir(parents=True)
        captured: dict[str, object] = {}

        def inspect_start(service: object) -> None:
            command = service.command  # type: ignore[attr-defined]
            init_option = next(
                item for item in command if item.startswith("--init-file=")
            )
            init_path = Path(init_option.partition("=")[2])
            captured["command"] = command
            captured["path"] = init_path
            captured["sql"] = init_path.read_text(encoding="utf-8")

        verified = Mock(returncode=0, stdout="1\n", stderr="")
        with (
            patch.object(Controller, "start_service", side_effect=inspect_start),
            patch.object(Controller, "stop_service") as stop,
            patch.object(Controller, "_mysql_query", return_value=verified) as query,
        ):
            controller.initialize_mysql()

        command = captured["command"]
        self.assertNotIn(controller.secret_values["mysql_password"], " ".join(command))
        self.assertIn("'root'@'127.0.0.1'", captured["sql"])
        self.assertIn("CREATE DATABASE IF NOT EXISTS rag_flow", captured["sql"])
        self.assertFalse(captured["path"].exists())
        query.assert_called_once()
        stop.assert_called_once_with("mysql", quiet=True)

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

    def test_elasticsearch_seal_allows_only_legacy_runtime_logs(self) -> None:
        controller = Controller(self.root)
        elasticsearch = controller.app / "services" / "elasticsearch"

        with patch("local_llm_ctl.validate_tree_seal") as validate:
            controller.verify_tree_seals()

        call = next(
            item
            for item in validate.call_args_list
            if item.args[0] == elasticsearch
        )
        self.assertEqual(
            call.args[2], (".local-llm-artifact.txt", "logs/")
        )

    def test_incomplete_tree_seal_is_reported_before_hashing(self) -> None:
        tree = self.root / "sealed-incomplete"
        tree.mkdir()
        (tree / "payload.bin").write_bytes(b"payload")
        marker = self.root / "incomplete.ok"
        marker.write_text("artifact=vendor.zip\n", encoding="utf-8")

        with self.assertRaisesRegex(ControlError, "Tree seal is incomplete"):
            validate_tree_seal(tree, marker)

    def test_runtime_asset_verification_uses_packaged_provenance_record(self) -> None:
        controller = Controller(self.root)
        controller.logs_dir.mkdir(parents=True, exist_ok=True)
        completed = Mock(returncode=0)

        with (
            patch.object(Controller, "portable_environment", return_value={}),
            patch("local_llm_ctl.subprocess.run", return_value=completed) as run,
        ):
            controller.verify_runtime_assets()

        asset_check = run.call_args_list[2].args[0]
        record_index = asset_check.index("--record") + 1
        self.assertEqual(
            Path(asset_check[record_index]),
            self.root / "app" / "config" / "ragflow-assets.json",
        )
        self.assertNotIn("ragflow-assets-verify.json", asset_check)

    def test_finalize_resumes_after_gpu_when_mysql_step_failed(self) -> None:
        controller = Controller(self.root)
        controller.ensure_config()

        with (
            patch.object(Controller, "service_running", return_value=False),
            patch.object(Controller, "verify_tree_seals") as verify_seals,
            patch.object(
                Controller, "install_resume_identity", return_value="payload-1"
            ),
            patch.object(Controller, "verify_runtime_assets") as verify_runtime,
            patch.object(Controller, "verify_gpu") as verify_gpu,
            patch.object(
                Controller,
                "initialize_mysql",
                side_effect=(ControlError("mysql failed"), None),
            ) as initialize_mysql,
        ):
            with self.assertRaisesRegex(ControlError, "mysql failed"):
                controller.finalize_install()
            self.assertTrue(controller.install_progress_marker.is_file())

            controller.finalize_install()

        self.assertEqual(verify_seals.call_count, 2)
        verify_runtime.assert_called_once_with()
        verify_gpu.assert_called_once_with()
        self.assertEqual(initialize_mysql.call_count, 2)
        self.assertTrue(controller.install_marker.is_file())
        self.assertFalse(controller.install_progress_marker.exists())


if __name__ == "__main__":
    unittest.main()
