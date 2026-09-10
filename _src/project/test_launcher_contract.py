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

    def test_offline_payload_files_are_checked_by_attributes(self) -> None:
        launcher = LAUNCHER.read_text(encoding="utf-8")
        self.assertNotIn('if exist "%BUNDLE_DIR%\\%%~F\\."', launcher)
        self.assertIn(
            'call :IS_REGULAR_FILE "%BUNDLE_DIR%\\%%~F"',
            launcher,
        )
        self.assertIn('set "REGULAR_FILE_ATTRIBUTES=%%~aI"', launcher)
        self.assertIn(
            'if /i "%REGULAR_FILE_ATTRIBUTES:~0,1%"=="d"',
            launcher,
        )
        self.assertIn(
            'if /i not "%REGULAR_FILE_ATTRIBUTES:l=%"=="%REGULAR_FILE_ATTRIBUTES%"',
            launcher,
        )

    def test_bootstrap_extractor_is_not_overwritten_while_running(self) -> None:
        launcher = LAUNCHER.read_text(encoding="utf-8")
        bootstrap_test = (
            '"%BUNDLE_DIR%\\7zr.exe" t '
            '"%BUNDLE_DIR%\\00-bootstrap-tools.7z"'
        )
        bootstrap_extract = (
            '"%BUNDLE_DIR%\\7zr.exe" x -y "-o%INSTALL_STAGE%" '
            '"%BUNDLE_DIR%\\00-bootstrap-tools.7z"'
        )
        remaining_start = launcher.index(
            "echo [STEP] Test and extract the remaining eight payload archives"
        )
        remaining_end = launcher.index("\n\nfor %%K in (", remaining_start)
        remaining_loop = launcher[remaining_start:remaining_end]

        self.assertIn(bootstrap_test, launcher)
        self.assertIn(bootstrap_extract, launcher)
        self.assertLess(launcher.index(bootstrap_test), launcher.index(bootstrap_extract))
        self.assertNotIn("00-bootstrap-tools.7z", remaining_loop)
        self.assertEqual(remaining_loop.count(".7z\""), 8)

    def test_offline_launcher_redirects_bytecode_before_running_sealed_python(
        self,
    ) -> None:
        launcher = LAUNCHER.read_text(encoding="utf-8")
        run_control = launcher[
            launcher.index("\n:RUN_CONTROL") : launcher.index("\n:INSTALL")
        ]
        portable_env = launcher[
            launcher.index("\n:SET_PORTABLE_ENV") : launcher.index("\n:USAGE")
        ]
        redirect = 'set "PYTHONPYCACHEPREFIX=%APP%\\cache\\python-bytecode"'

        self.assertIn("call :SET_PORTABLE_ENV", run_control)
        env_call = run_control.index("call :SET_PORTABLE_ENV")
        self.assertLess(
            env_call,
            run_control.index(
                '"%APP%\\runtime\\python-rag\\python.exe"', env_call
            ),
        )
        self.assertIn(redirect, portable_env)

    def test_offline_launcher_reseals_fresh_immutable_trees_before_publish(
        self,
    ) -> None:
        launcher = LAUNCHER.read_text(encoding="utf-8")
        seal_call = "call :SEAL_STAGED_IMMUTABLE_TREES"
        publish = "echo [STEP] Atomically publish the installed app directory"

        self.assertIn(seal_call, launcher)
        self.assertLess(launcher.index(seal_call), launcher.index(publish))
        self.assertIn(":SEAL_STAGED_TREE", launcher)
        self.assertIn('tree_fingerprint.ps1" -Root "%TREE_ROOT%"', launcher)
        self.assertIn('echo tree_sha256=%TREE_SHA256_VALUE%', launcher)
        self.assertIn('echo tree_file_count=%TREE_FILE_COUNT_VALUE%', launcher)
        self.assertIn(
            'call :SEAL_STAGED_TREE '
            '"%INSTALL_STAGE%\\app\\services\\elasticsearch" '
            '".local-llm-artifact.txt;logs/"',
            launcher,
        )

    def test_current_controller_can_overlay_legacy_archives(self) -> None:
        launcher = LAUNCHER.read_text(encoding="utf-8")
        prepare = PREPARE.read_text(encoding="utf-8")

        self.assertIn(":OVERLAY_CONTROL_HELPER", launcher)
        self.assertIn('"%ROOT%\\local_llm_ctl.py"', launcher)
        self.assertIn(
            'copy /y "%PROJECT%\\local_llm_ctl.py" '
            '"%PACKAGE_STAGE%\\local_llm_ctl.py"',
            prepare,
        )
        self.assertIn("'local_llm_ctl.py'", prepare)

    def test_online_and_offline_asset_verification_use_packaged_record(self) -> None:
        launcher_controller = (
            ROOT / "_src" / "project" / "local_llm_ctl.py"
        ).read_text(encoding="utf-8")
        prepare = PREPARE.read_text(encoding="utf-8")

        self.assertIn(
            'self.ragflow_asset_record = self.app / "config" / "ragflow-assets.json"',
            launcher_controller,
        )
        self.assertIn(
            '--record "%~1\\config\\ragflow-assets.json" --verify-only',
            prepare,
        )
        self.assertNotIn("ragflow-assets-verify.json", launcher_controller)

    def test_manual_artifact_destination_is_accepted_before_stale_replacement(self) -> None:
        prepare = PREPARE.read_text(encoding="utf-8")
        accept = 'Accepted manually prepared destination for !ART_NAME!: !ART_KEY!'
        replace = "Replacing stale or untracked destination for !ART_NAME!."
        self.assertIn(accept, prepare)
        self.assertIn(replace, prepare)
        self.assertLess(prepare.index(accept), prepare.index(replace))

    def test_interrupted_artifact_backup_is_moved_aside_before_retry(self) -> None:
        prepare = PREPARE.read_text(encoding="utf-8")
        self.assertNotIn(
            "A preserved destination from an interrupted attempt exists",
            prepare,
        )
        self.assertIn(":MoveInterruptedArtifactBackupAside", prepare)
        self.assertIn("artifact-orphaned-%ART_NAME%-%RANDOM%-%RANDOM%", prepare)
        self.assertIn('call :MoveInterruptedArtifactBackupAside', prepare)
        self.assertIn('call :DirectoryIsEmpty "!ART_DEST!"', prepare)

    def test_online_prepare_startup_preserves_resumable_outputs(self) -> None:
        prepare = PREPARE.read_text(encoding="utf-8")
        self.assertIn("call :ValidateMutableResumeState", prepare)
        self.assertIn('set "RESUME_GRAPH_VERSION=', prepare)
        self.assertIn(
            "launcher-only fixes do not invalidate completed runtimes",
            prepare,
        )
        self.assertIn(":ReportMutableResumeTree", prepare)
        self.assertNotIn("call :ValidateOrResetMutableTrees", prepare)
        self.assertNotIn("restoring its clean base", prepare)
        self.assertNotIn("RAG_RUNTIME_RESET", prepare)
        self.assertNotIn("RAG_SOURCE_RESET", prepare)
        self.assertNotIn("OCR_RUNTIME_RESET", prepare)
        startup = prepare[
            prepare.index("\n:ValidateMutableResumeState") : prepare.index(
                "\n:MutableTreeMatches"
            )
        ]
        self.assertNotIn("RemoveTreeChecked", startup)
        self.assertNotIn("TreeMatchesMarker", startup)
        self.assertIn("preserving completed step outputs", startup)

    def test_online_prepare_has_fingerprinted_resume_markers_for_long_steps(
        self,
    ) -> None:
        prepare = PREPARE.read_text(encoding="utf-8")
        for required in (
            "shared-runtime-dlls.ok",
            "ragflow-assets.ok",
            "ragflow-runtime-smoke.ok",
            "ocr-gateway-contract.ok",
            "ocr-gpu-smoke.ok",
            "probe-portable-binaries.ok",
            "audit-portable-runtimes.ok",
            "[SKIP] App-local Microsoft VC runtime already extracted.",
            "[SKIP] RAGFlow models, NLTK, tiktoken and Tika assets already prepared.",
            "[SKIP] RAGFlow runtime smoke already passed.",
            "[SKIP] OCR gateway contract already passed.",
            "[SKIP] Portable service and inference executable probes already passed.",
            "[SKIP] Portable runtime audit already passed.",
            ":ValidateRagflowAssetsPrepared",
            ":PortableBinaryKeysPresent",
        ):
            self.assertIn(required, prepare)

    def test_online_prepare_seals_and_rechecks_immutable_payloads(self) -> None:
        prepare = PREPARE.read_text(encoding="utf-8")
        seal = "call :SealImmutableArtifactTrees"
        package = "call :PackagePreparedOutput"
        verify = "call :VerifyRehydratedImmutableSeals"
        payload_verify = "call :VerifyRehydratedPayload"

        self.assertIn(seal, prepare)
        self.assertLess(prepare.index(seal), prepare.index(package))
        self.assertGreaterEqual(prepare.count(verify), 2)
        self.assertLess(prepare.index(verify), prepare.index(payload_verify))
        self.assertGreater(prepare.rindex(verify), prepare.index(payload_verify))
        self.assertIn(
            'call :WriteArtifactMarker "%ARTIFACT_MARKER%" '
            '"%ARTIFACT_NAME%" "%ARTIFACT_TREE_SHA256%" '
            '"%ARTIFACT_TREE_FILE_COUNT%"',
            prepare,
        )
        self.assertIn(
            'call :SealArtifactTree "%APP%\\services\\elasticsearch" '
            '".local-llm-artifact.txt;logs/"',
            prepare,
        )
        self.assertIn(
            'call :TreeMatchesMarker '
            '"%REHYDRATE_APP%\\services\\elasticsearch" '
            '"%REHYDRATE_APP%\\services\\elasticsearch\\.local-llm-artifact.txt" '
            '".local-llm-artifact.txt;logs/"',
            prepare,
        )

    def test_online_prepare_removes_install_local_state_before_sealing(self) -> None:
        prepare = PREPARE.read_text(encoding="utf-8")
        cleanup_call = "call :PreparePortableSeed"
        mutable_seal = "call :SealMutableRuntimeTrees"
        package = "call :PackagePreparedOutput"
        cleanup = prepare[
            prepare.index("\n:PreparePortableSeed") : prepare.index(
                "\n:SealMutableRuntimeTrees"
            )
        ]

        self.assertLess(prepare.index(cleanup_call), prepare.index(mutable_seal))
        self.assertLess(prepare.index(cleanup_call), prepare.index(package))
        self.assertIn(
            'call :RemoveTreeChecked "%APP%\\config\\runtime"', cleanup
        )
        self.assertIn(
            'call :RemoveRegularFileChecked '
            '"%RAGFLOW_DIR%\\conf\\local.service_conf.yaml"',
            cleanup,
        )
        self.assertIn('call :RemoveTreeChecked "%RAGFLOW_DIR%\\logs"', cleanup)

    def test_rehydrate_smoke_does_not_leave_build_path_bytecode(self) -> None:
        prepare = PREPARE.read_text(encoding="utf-8")
        verify = prepare[
            prepare.index("\n:VerifyRehydratedPayload") : prepare.index(
                "\n:ProbeRehydratedBinaries"
            )
        ]
        audit = (
            '"%~1\\runtime\\python-rag\\python.exe" '
            '"%~1\\config\\project\\audit_portability.py"'
        )
        cleanup = 'call :RemoveTreeChecked "%~1\\cache\\python-bytecode"'

        self.assertIn('set "PYTHONDONTWRITEBYTECODE=1"', verify)
        self.assertIn(cleanup, verify)
        self.assertIn(audit, verify)
        self.assertLess(verify.index(cleanup), verify.index(audit))

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

    def test_tree_fingerprint_hashes_windows_long_paths_without_resolve_path(
        self,
    ) -> None:
        script = (ROOT / "_src" / "project" / "tree_fingerprint.ps1").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("Get-FileHash", script)
        self.assertIn("Get-FileSha256LongPath", script)
        self.assertIn("[IO.File]::Open", script)
        self.assertIn('return "\\\\?\\" + [IO.Path]::GetFullPath($Path)', script)

    def test_remove_tree_has_a_long_path_fallback(self) -> None:
        prepare = PREPARE.read_text(encoding="utf-8")
        remover = ROOT / "_src" / "project" / "remove_tree.ps1"
        self.assertTrue(remover.is_file())
        script = remover.read_text(encoding="utf-8")
        self.assertIn('-File "%PROJECT%\\remove_tree.ps1"', prepare)
        self.assertIn('"remove_tree.ps1"', prepare)
        self.assertIn("call :CleanupInterruptedRehydrateTrees", prepare)
        self.assertIn('for /d %%D in ("%WORK%\\rehydrate-*")', prepare)
        self.assertIn("[IO.Directory]::EnumerateFileSystemEntries", script)
        self.assertIn("Refusing to remove a volume root", script)
        self.assertIn("[IO.FileAttributes]::ReparsePoint", script)


if __name__ == "__main__":
    unittest.main()
