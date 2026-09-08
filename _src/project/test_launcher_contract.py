#!/usr/bin/env python3
"""Static contracts shared by the online packager and offline BAT launcher."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PREPARE = ROOT / "1.PREPARE-ONLINE.bat"
LAUNCHER = ROOT / "LOCAL-LLM.bat"

PAYLOAD = {
    "00-bootstrap-tools.7z",
    "10-python-rag-runtime.7z",
    "20-python-ocr-gpu-runtime.7z",
    "21-paddle-models.7z",
    "30-ragflow-backend.7z",
    "31-ragflow-web-dist.7z",
    "40-services.7z",
    "41-config-seed.7z",
    "50-llama-vulkan-runtime.7z",
    "7zr.exe",
    "LOCAL-LLM.bat",
    "README.md",
}


class LauncherContractTest(unittest.TestCase):
    def test_versions_and_payload_manifest_stay_synchronized(self) -> None:
        prepare = PREPARE.read_text(encoding="utf-8")
        launcher = LAUNCHER.read_text(encoding="utf-8")
        prepare_version = re.search(r'set "PROJECT_VERSION=([^"\r\n]+)', prepare)
        control_version = re.search(r'set "CONTROL_VERSION=([^"\r\n]+)', launcher)
        self.assertIsNotNone(prepare_version)
        self.assertIsNotNone(control_version)
        self.assertEqual(prepare_version.group(1), control_version.group(1))
        controller = (ROOT / "_src" / "project" / "local_llm_ctl.py").read_text(
            encoding="utf-8"
        )
        python_version = re.search(r'CONTROL_VERSION = "([^"\r\n]+)', controller)
        self.assertIsNotNone(python_version)
        self.assertEqual(control_version.group(1), python_version.group(1))
        for name in PAYLOAD:
            self.assertIn(name, prepare)
            self.assertIn(name, launcher)

    def test_all_literal_batch_targets_exist(self) -> None:
        for path in (PREPARE, LAUNCHER):
            text = path.read_text(encoding="utf-8")
            labels = {
                match.group(1).lower()
                for match in re.finditer(r"(?im)^:([a-z0-9_.-]+)\s*$", text)
            }
            references = {
                match.group(1).lower()
                for match in re.finditer(r"(?i)\b(?:call|goto)\s+:([a-z0-9_.-]+)", text)
            }
            self.assertEqual(set(), references - labels, path.name)

    def test_external_payloads_are_presence_checked_without_hash_manifests(self) -> None:
        prepare = PREPARE.read_text(encoding="utf-8")
        launcher = LAUNCHER.read_text(encoding="utf-8")
        combined = prepare + launcher
        for removed_contract in (
            "artifacts.sha256",
            "SHA256SUMS.txt",
            "SOURCE-SHA256SUMS.txt",
            ":VerifyArtifactHash",
        ):
            self.assertNotIn(removed_contract, combined)
        self.assertIn('if not exist "!ART_SOURCE!" (', prepare)
        self.assertNotIn('if exist "!ART_SOURCE!\\." (', prepare)

    def test_artifact_key_is_checked_by_attributes(self) -> None:
        prepare = PREPARE.read_text(encoding="utf-8")
        launcher = LAUNCHER.read_text(encoding="utf-8")
        self.assertNotIn('if not exist "!ART_KEY!\\."', prepare)
        self.assertGreaterEqual(
            prepare.count('call :IsRegularFile "!ART_KEY!"'),
            3,
        )
        self.assertIn('set "REGULAR_FILE_ATTRIBUTES=%%~aI"', prepare)
        self.assertNotIn("Required artifact path is a directory", prepare)
        self.assertIn("Source file is present: !ART_NAME!", prepare)
        self.assertIn("All required offline payload files are present.", launcher)

    def test_manual_artifact_destination_is_accepted_before_stale_replacement(self) -> None:
        prepare = PREPARE.read_text(encoding="utf-8")
        accept = 'Accepted manually prepared destination for !ART_NAME!: !ART_KEY!'
        replace = "Replacing stale or untracked destination for !ART_NAME!."
        self.assertIn(accept, prepare)
        self.assertIn(replace, prepare)
        self.assertLess(prepare.index(accept), prepare.index(replace))

    def test_portable_policy_has_no_machine_mutations(self) -> None:
        combined = (
            PREPARE.read_text(encoding="utf-8") + LAUNCHER.read_text(encoding="utf-8")
        ).lower()
        for forbidden in (
            "setx ",
            "reg add",
            "sc.exe create",
            "new-service",
            "docker ",
            "wsl ",
        ):
            self.assertNotIn(forbidden, combined)

    def test_online_prepare_streams_long_step_logs_and_isolates_npm(self) -> None:
        prepare = PREPARE.read_text(encoding="utf-8")
        for required in (
            ":BeginStepLog",
            ":StartLiveLog",
            ":PrintLogTail",
            'if /i "%LOCAL_LLM_LIVE_LOGS%"=="0"',
            'if /i "%LOCAL_LLM_LIVE_LOGS%"=="off"',
            'if /i "%LOCAL_LLM_LIVE_LOGS%"=="false"',
            'call :BeginStepLog "%WEB_BUILD_LOG%"',
            '"%ComSpec%" /d /s /c ""%NODE_DIR%\\npm.cmd" ci --no-audit --no-fund"',
            '"%ComSpec%" /d /s /c ""%NODE_DIR%\\npm.cmd" run build"',
            'move "%RAGFLOW_DIR%\\web\\dist" "%APP%\\web" >>"%WEB_BUILD_LOG%" 2>&1',
            '"tree_fingerprint.ps1"',
            '-File "%PROJECT%\\tree_fingerprint.ps1"',
            "Could not move the built RAGFlow web dist into app\\web.",
            "Moved RAGFlow web dist is missing index.html",
            "Could not fingerprint the prepared RAGFlow web dist.",
            'set "TREE_ERROR_OUTPUT=%TREE_OUTPUT%.err"',
            "ONLINE PREPARATION FAILED with exit code",
        ):
            self.assertIn(required, prepare)
        self.assertNotIn("Get-ChildItem -LiteralPath $rootPath", prepare)
        self.assertNotIn('+"`0"+', prepare)
        tree_fingerprint = ROOT / "_src" / "project" / "tree_fingerprint.ps1"
        self.assertTrue(tree_fingerprint.is_file())
        script = tree_fingerprint.read_text(encoding="utf-8")
        self.assertIn("$rel + [char]0 + $item.Length + [char]0 + $fileHash", script)


if __name__ == "__main__":
    unittest.main()
