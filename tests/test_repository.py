import importlib.util
import json
from pathlib import Path
import unittest


class RepositoryContractTests(unittest.TestCase):
    def test_modular_repository_contract(self) -> None:
        script = Path(__file__).parents[1] / "scripts" / "validate_repository.py"
        spec = importlib.util.spec_from_file_location("validate_repository", script)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader if spec else None)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        self.assertEqual(module.validate(), [])

    def test_root_prepare_menu_dispatches_to_every_module_launcher(self) -> None:
        root = Path(__file__).parents[1]
        launcher = (root / "1.PREPARE-ONLINE.bat").read_text(encoding="utf-8")
        self.assertIn("choice /C 123456780", launcher)
        for name in (
            "mysql",
            "elasticsearch",
            "silo",
            "valkey",
            "llama-cpp",
            "paddleocr",
            "ragflow",
            "web",
        ):
            with self.subTest(module=name):
                self.assertIn(f'set "MODULE={name}"', launcher)
        self.assertIn('call "%~dp0modules\\%MODULE%\\PREPARE-ONLINE.bat"', launcher)

    def test_source_archives_are_shared_but_build_caches_stay_module_local(self) -> None:
        root = Path(__file__).parents[1]
        for prepare_path in sorted((root / "modules").glob("*/src/prepare.ps1")):
            with self.subTest(module=prepare_path.parents[1].name):
                prepare = prepare_path.read_text(encoding="utf-8")
                self.assertIn("$SourceRoot = Join-Path $Root '_src'", prepare)
                self.assertIn("$CacheRoot = Join-Path $ModuleRoot '_src'", prepare)
                self.assertIn("$PreparedRoot = Join-Path $Root 'prepared'", prepare)
                self.assertIn("$OnlineRoot=Join-Path $CacheRoot '_online'", prepare)
                self.assertIn("-SourceRoot $SourceRoot -CacheRoot $CacheRoot", prepare)
                self.assertTrue((prepare_path.parents[1] / "_src" / ".gitkeep").is_file())
                self.assertFalse((prepare_path.parents[1] / "prepared").exists())
        self.assertFalse((root / "stack" / "prepared").exists())

        expected_logs = {
            "paddleocr": "build-ocr.log",
            "ragflow": "build-ragflow.log",
            "web": "build-web.log",
        }
        for name, log_name in expected_logs.items():
            with self.subTest(cache=name):
                hook = (root / "modules" / name / "src" / "build-hook.ps1").read_text(
                    encoding="utf-8"
                )
                self.assertIn("[string]$CacheRoot", hook)
                self.assertIn(f"$Log=Join-Path $CacheRoot '{log_name}'", hook)

    def test_native_build_logs_do_not_turn_stderr_into_fatal_errors(self) -> None:
        modules = Path(__file__).parents[1] / "modules"
        for name in ("paddleocr", "ragflow", "web"):
            with self.subTest(module=name):
                hook = (modules / name / "src" / "build-hook.ps1").read_text(
                    encoding="utf-8"
                )
                self.assertIn("function Invoke-LoggedNative", hook)
                self.assertIn("$ErrorActionPreference='Continue'", hook)
                self.assertEqual(hook.count("*>>$Log"), 1)

    def test_ragflow_avoids_uv_pe_launcher_creation_during_bulk_install(self) -> None:
        hook = (
            Path(__file__).parents[1]
            / "modules"
            / "ragflow"
            / "src"
            / "build-hook.ps1"
        ).read_text(encoding="utf-8")
        pip_install = (
            "@('-m','pip','install','--no-index','--find-links',$Wheelhouse,"
            "'--no-deps','--only-binary=:all:','--no-compile',"
            "'--requirement',$WheelLock)"
        )
        uv_sync = (
            "@('pip','sync','--python',$Python,'--no-index','--find-links',"
            "$Wheelhouse,$WheelLock)"
        )
        self.assertIn("Remove-Item -LiteralPath $Log", hook)
        self.assertIn(pip_install, hook)
        self.assertIn(uv_sync, hook)
        self.assertLess(hook.index(pip_install), hook.index(uv_sync))
        self.assertIn("'--no-hashes'", hook)
        self.assertNotIn("--generate-" + "hashes", hook)
        self.assertNotIn("--require-" + "hashes", hook)

    def test_ragflow_preparation_has_no_content_integrity_gates(self) -> None:
        source = Path(__file__).parents[1] / "modules" / "ragflow" / "src"
        python_sources = {
            name: (source / name).read_text(encoding="utf-8")
            for name in (
                "prepare_ragflow_assets.py",
                "prepare_ragflow_windows.py",
                "prepare_wheelhouse_lock.py",
                "verify_ragflow_runtime.py",
            )
        }
        for name, text in python_sources.items():
            with self.subTest(source=name):
                self.assertNotIn("import hash" + "lib", text)
                self.assertNotIn("SHA" + "256", text)
        for name in ("ragflow-windows-additions.txt", "ragflow-windows-overrides.txt"):
            with self.subTest(source=name):
                text = (source / name).read_text(encoding="utf-8")
                self.assertNotIn("--" + "hash=", text)

    def test_python_import_probes_survive_windows_powershell_quoting(self) -> None:
        modules = Path(__file__).parents[1] / "modules"
        for name in ("paddleocr", "ragflow"):
            with self.subTest(module=name):
                control = (modules / name / "control.ps1").read_text(
                    encoding="utf-8"
                )
                self.assertNotIn('print("', control)
                self.assertIn("& $Python -c 'import ", control)

    def test_llama_embedding_uses_same_gpu_for_chat_and_ingestion(self) -> None:
        control = (
            Path(__file__).parents[1] / "modules" / "llama-cpp" / "control.ps1"
        ).read_text(encoding="utf-8")
        install = control[control.index("function Install-Llama{") :]
        install = install[: install.index("function Model")]
        start = control[control.index("function Start-Embedding") :]
        start = start[: start.index("function Start-Chat")]
        self.assertIn("embedding_gpu_chat=0", install)
        self.assertNotIn("embedding_gpu_ingestion", install)
        self.assertIn("$gpu=$s.embedding_gpu_chat", start)
        self.assertNotIn("embedding_gpu_ingestion", start)

    def test_mysql_readiness_probe_tolerates_transient_native_stderr(self) -> None:
        control = (
            Path(__file__).parents[1] / "modules" / "mysql" / "control.ps1"
        ).read_text(encoding="utf-8")
        quiet = control[control.index("function Invoke-MySqlQuietly") :]
        quiet = quiet[: quiet.index("function Probe-MySql")]
        self.assertIn("$ErrorActionPreference='Continue'", quiet)
        self.assertIn("return $LASTEXITCODE", quiet)
        self.assertIn(
            "$ErrorActionPreference=$PreviousErrorActionPreference", quiet
        )
        self.assertIn(
            "Invoke-MySqlQuietly",
            control[control.index("function Probe-MySql") :],
        )

    def test_elasticsearch_runtime_logs_are_declared_mutable(self) -> None:
        manifest = json.loads(
            (
                Path(__file__).parents[1]
                / "modules"
                / "elasticsearch"
                / "module.json"
            ).read_text(encoding="utf-8")
        )
        self.assertIn("runtime/logs/", manifest["mutable_paths"])

    def test_elasticsearch_readiness_failure_prints_log_excerpts(self) -> None:
        runtime = (
            Path(__file__).parents[1]
            / "modules"
            / "elasticsearch"
            / "lib"
            / "runtime.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("function Write-ProcessDiagnostics", runtime)
        self.assertIn("Get-Content -LiteralPath $Path -Tail $Lines", runtime)
        self.assertIn("Write-ProcessDiagnostics $Name;throw", runtime)
        self.assertIn("see log excerpts above", runtime)

    def test_elasticsearch_install_smoke_starts_runtime(self) -> None:
        control = (
            Path(__file__).parents[1] / "modules" / "elasticsearch" / "control.ps1"
        ).read_text(encoding="utf-8")
        install = control[control.index("function Install-Elastic{") :]
        install = install[: install.index("function Start-Elastic")]
        self.assertIn("try{Start-Elastic}", install)
        self.assertIn(
            "finally{if(Get-OwnedProcess 'elasticsearch'){Stop-OwnedProcess 'elasticsearch'}}",
            install,
        )

    def test_elasticsearch_starts_from_runtime_with_gc_log_directory(self) -> None:
        control = (
            Path(__file__).parents[1] / "modules" / "elasticsearch" / "control.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("$EsRuntimeLogs=Join-Path $Runtime 'logs'", control)
        write_config = control[control.index("function Write-ElasticConfig{") :]
        write_config = write_config[: write_config.index("function Install-Elastic")]
        self.assertIn("$EsRuntimeLogs", write_config)
        start = control[control.index("function Start-Elastic{") :]
        start = start[: start.index("switch($CommandName")]
        self.assertIn(
            "Start-OwnedProcess 'elasticsearch' $env:ComSpec @('/d','/c',(Join-Path $Runtime 'bin\\elasticsearch.bat')) $Runtime",
            start,
        )

    def test_process_stop_tolerates_exit_between_probe_and_taskkill(self) -> None:
        modules = Path(__file__).parents[1] / "modules"
        for runtime_path in sorted(modules.glob("*/lib/runtime.ps1")):
            with self.subTest(module=runtime_path.parents[1].name):
                runtime = runtime_path.read_text(encoding="utf-8")
                self.assertIn(
                    "$LASTEXITCODE -ne 0 -and (Get-OwnedProcess $Name)", runtime
                )

    def test_mysql_install_replays_bootstrap_after_an_interrupted_attempt(self) -> None:
        control = (
            Path(__file__).parents[1] / "modules" / "mysql" / "control.ps1"
        ).read_text(encoding="utf-8")
        install = control[control.index("function Install-MySql{") :]
        install = install[: install.index("switch($CommandName")]
        self.assertIn("if(Get-OwnedProcess 'mysql'){Stop-MySql}", install)
        self.assertIn(
            "try{Write-Config $s $sql;Start-OwnedProcess 'mysql'", install
        )
        self.assertIn(
            "finally{if(Get-OwnedProcess 'mysql'){Stop-OwnedProcess 'mysql'}",
            install,
        )

    def test_mysql_bootstrap_waits_for_the_owned_mysql_process(self) -> None:
        control = (
            Path(__file__).parents[1] / "modules" / "mysql" / "control.ps1"
        ).read_text(encoding="utf-8")
        install = control[control.index("function Install-MySql{") :]
        install = install[: install.index("switch($CommandName")]
        self.assertIn("Start-OwnedProcess 'mysql'", install)
        self.assertIn("Wait-Healthy 'mysql' {Probe-MySql}", install)
        self.assertNotIn("Wait-Healthy 'mysql-bootstrap'", install)

    def test_paddleocr_readiness_waits_for_the_owned_process(self) -> None:
        control = (
            Path(__file__).parents[1] / "modules" / "paddleocr" / "control.ps1"
        ).read_text(encoding="utf-8")
        start = control[control.index("function Start-Ocr{") :]
        start = start[: start.index("switch($CommandName")]
        self.assertIn("Start-OwnedProcess 'paddleocr'", start)
        self.assertIn("Wait-Healthy 'paddleocr' {", start)
        self.assertNotIn("Wait-Healthy 'paddleocr strict GPU API'", start)

    def test_standalone_paddleocr_profile_bundles_document_workbench(self) -> None:
        root = Path(__file__).parents[1]
        module = root / "modules" / "paddleocr"
        for name in (
            "document_workbench.py",
            "document_workbench.html",
            "test_document_workbench.py",
        ):
            self.assertTrue((module / "src" / name).is_file(), name)
        hook = (module / "src" / "build-hook.ps1").read_text(encoding="utf-8")
        control = (module / "control.ps1").read_text(encoding="utf-8")
        stack = (root / "stack" / "control.ps1").read_text(encoding="utf-8")
        launcher = (root / "LOCAL-LLM.bat").read_text(encoding="utf-8")
        self.assertIn("document_workbench.py", hook)
        self.assertIn("test_document_workbench.py", hook)
        self.assertIn("$WorkbenchPort=9400", control)
        self.assertIn("'workbench'{Assert-Installed;Start-OcrWorkbench", control)
        self.assertIn("'paddleocr'{Start-PaddleOcr}", stack)
        self.assertIn("start paddleocr", launcher)

    def test_ingestion_uses_separate_ocr_gpu_profile(self) -> None:
        stack = (
            Path(__file__).parents[1] / "stack" / "control.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("function Start-IngestionProfile", stack)
        self.assertIn("Run 'llama-cpp' 'start' 'ingestion'", stack)
        self.assertIn("function Start-Ingestion{Start-IngestionProfile 'ingestion'", stack)
        self.assertIn("function Start-IngestionCpu{Start-IngestionProfile 'cpu'", stack)
        self.assertIn("'ingestion-cpu'{Start-IngestionCpu}", stack)
        self.assertIn(
            "'devices'{Assert-Deployment;Run 'llama-cpp' 'devices';Run 'paddleocr' 'devices'}",
            stack,
        )

    def test_paddleocr_ingestion_prefers_second_cuda_gpu(self) -> None:
        control = (
            Path(__file__).parents[1] / "modules" / "paddleocr" / "control.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("gpu_index='auto'", control)
        self.assertIn("prefer_gpu_index=1", control)
        self.assertIn("ingestion_gpu_index='auto'", control)
        self.assertIn("text_recognition_batch_size=8", control)
        self.assertIn("function Sync-OcrServiceSource", control)
        self.assertIn("if($Target -eq 'cpu'){return 'cpu'}", control)
        self.assertIn("if($Target -eq 'ingestion')", control)
        self.assertIn("$env:LOCAL_OCR_REQUESTED_DEVICE=Resolve-OcrRequestedDevice $s $Target", control)
        self.assertIn("$env:LOCAL_OCR_REQUESTED_GPU_INDEX=$env:LOCAL_OCR_REQUESTED_DEVICE", control)
        self.assertIn("$RuntimeDevice=Resolve-OcrGpuRuntimeIndex $s $Target", control)
        self.assertIn("$env:LOCAL_OCR_ALLOW_CPU='1'", control)
        self.assertIn("Remove-Item Env:\\LOCAL_OCR_ALLOW_CPU", control)
        self.assertIn('$env:LOCAL_OCR_DEVICE="gpu:$RuntimeDevice"', control)
        self.assertIn("print(preferred if preferred < count else 0)", control)
        self.assertIn("$env:LOCAL_OCR_TEXT_REC_BATCH_SIZE", control)
        self.assertIn("'devices'{Show-OcrDevices}", control)
        show_devices = control[control.index("function Show-OcrDevices{") :]
        show_devices = show_devices[: show_devices.index("switch($CommandName")]
        self.assertIn("Set-OcrEnvironment $s 'ingestion'", show_devices)
        self.assertIn("import paddle", show_devices)
        self.assertIn("LOCAL_OCR_REQUESTED_DEVICE", show_devices)
        self.assertIn("LOCAL_OCR_REQUESTED_GPU_INDEX", show_devices)
        self.assertIn("resolved_gpu_index", show_devices)
        self.assertIn("$Code | & $Python -", show_devices)
        self.assertNotIn("$Python -c $Code", show_devices)
        self.assertNotIn("$Gateway", show_devices)
        self.assertNotIn("'--list-devices'", show_devices)

    def test_paddleocr_gateway_resolves_auto_gpu_and_recognition_batch(self) -> None:
        gateway = (
            Path(__file__).parents[1]
            / "modules"
            / "paddleocr"
            / "src"
            / "ocr_job_gateway.py"
        ).read_text(encoding="utf-8")
        self.assertIn("DEFAULT_TEXT_RECOGNITION_BATCH_SIZE = 8", gateway)
        self.assertIn('LOCAL_OCR_DEVICE",', gateway)
        self.assertIn('return "cpu", None, requested, "cpu-explicit"', gateway)
        self.assertIn('LOCAL_OCR_GPU_INDEX", "auto"', gateway)
        self.assertIn('LOCAL_OCR_PREFER_GPU_INDEX", "1"', gateway)
        self.assertIn('return f"gpu:{preferred}", preferred, requested, f"auto-prefer-{preferred}"', gateway)
        self.assertIn('return "gpu:0", 0, requested, "auto-fallback-0"', gateway)
        self.assertIn('if requested.startswith("gpu:"):', gateway)
        self.assertIn('os.environ.get("LOCAL_OCR_ALLOW_CPU") != "1"', gateway)
        self.assertIn("OCR resolved to CPU", gateway)
        self.assertIn('"runtime_device": paddle.device.get_device()', gateway)
        self.assertIn('"text_recognition_batch_size": text_recognition_batch_size', gateway)
        self.assertIn('parser.add_argument("--list-devices", action="store_true")', gateway)

    def test_ragflow_worker_caps_embedding_to_llama_context(self) -> None:
        root = Path(__file__).parents[1]
        control = (root / "modules" / "ragflow" / "control.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("function Ensure-EmbeddingContextPatch", control)
        self.assertIn("Ensure-EmbeddingContextPatch;$ocr=Connection", control)
        self.assertIn("LOCAL_RAGFLOW_EMBEDDING_MAX_TOKENS", control)
        self.assertIn(
            "llama-cpp\\config\\runtime\\settings.json",
            control,
        )
        self.assertIn(
            "$env:LOCAL_RAGFLOW_EMBEDDING_MAX_TOKENS=[string]$llamaSettings.embedding_context",
            control,
        )

        prepare = (
            root / "modules" / "ragflow" / "src" / "prepare_ragflow_windows.py"
        ).read_text(encoding="utf-8")
        self.assertIn("def patch_local_embedding_context_limit", prepare)
        self.assertIn("LOCAL_RAGFLOW_EMBEDDING_MAX_TOKENS", prepare)
        self.assertIn("LLMType.EMBEDDING.value", prepare)

    def test_ragflow_bundles_portable_java_for_tika(self) -> None:
        root = Path(__file__).parents[1]
        manifest = json.loads(
            (root / "modules" / "ragflow" / "module.json").read_text(
                encoding="utf-8"
            )
        )
        java = next(
            artifact
            for artifact in manifest["artifacts"]
            if artifact["file"] == "OpenJDK21U-jre_x64_windows_hotspot_21.0.8_9.zip"
        )
        self.assertEqual(java["target"], "runtime/java")
        self.assertEqual(java["key"], "runtime/java/bin/java.exe")

        control = (root / "modules" / "ragflow" / "control.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("$JavaExe=Join-Path $JavaHome 'bin\\java.exe'", control)
        self.assertIn("$env:JAVA_HOME=$JavaHome", control)
        self.assertIn("$env:TIKA_JAVA=$JavaExe", control)
        self.assertIn("$env:TIKA_PATH=$tikaTemp", control)
        self.assertIn("$env:TIKA_LOG_PATH=$LogRoot", control)
        self.assertIn("function Assert-TikaRuntime", control)
        tika_probe = control[control.index("function Assert-TikaRuntime{") :]
        tika_probe = tika_probe[: tika_probe.index("function Assert-CoreDependencies")]
        self.assertIn("$ErrorActionPreference='Continue'", tika_probe)
        self.assertIn("$JavaExitCode=$LASTEXITCODE", tika_probe)
        self.assertIn(
            "$ErrorActionPreference=$PreviousErrorActionPreference", tika_probe
        )
        self.assertIn("if($JavaExitCode -ne 0)", tika_probe)
        self.assertIn("Assert-TikaRuntime;Start-OwnedProcess 'task-executor'", control)

        prepare = (
            root / "modules" / "ragflow" / "src" / "prepare_ragflow_windows.py"
        ).read_text(encoding="utf-8")
        self.assertIn("def patch_tika_windows_java_launch", prepare)
        self.assertIn("list2cmdline([java_path])", prepare)
        self.assertIn("Popen([java_path]", prepare)

    def test_web_caddyfile_uses_multiline_handle_blocks(self) -> None:
        control = (
            Path(__file__).parents[1] / "modules" / "web" / "control.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "handle /v1/* {\n        reverse_proxy ",
            control,
        )
        self.assertIn(
            "handle /api/* {\n        reverse_proxy ",
            control,
        )
        self.assertNotRegex(control, r"handle /(?:v1|api)/\* \{[^\r\n]+\}")

    def test_ragflow_archives_exclude_upstream_development_symlinks(self) -> None:
        root = Path(__file__).parents[1]
        expected = [
            "ragflow-0.27.1/CLAUDE.md",
            "ragflow-0.27.1/internal/deepdoc/parser/pdf/tool/testdata",
        ]
        for module_name in ("ragflow", "web"):
            with self.subTest(module=module_name):
                module_root = root / "modules" / module_name
                manifest = json.loads(
                    (module_root / "module.json").read_text(encoding="utf-8")
                )
                source = next(
                    artifact
                    for artifact in manifest["artifacts"]
                    if artifact["file"] == "ragflow-0.27.1.zip"
                )
                self.assertEqual(source["extract_excludes"], expected)
                prepare = (module_root / "src" / "prepare.ps1").read_text(
                    encoding="utf-8"
                )
                self.assertIn("[string[]]$ExcludeEntries", prepare)
                self.assertIn('"-x!$Entry"', prepare)

    def test_ragflow_wheel_build_avoids_prefixed_bytecode_paths(self) -> None:
        hook = (
            Path(__file__).parents[1] / "modules" / "ragflow" / "src" / "build-hook.ps1"
        ).read_text(encoding="utf-8")
        wheel_command = "@('-m','pip','wheel'"
        self.assertIn("$PreviousPythonPycCachePrefix=$env:PYTHONPYCACHEPREFIX", hook)
        self.assertLess(
            hook.index("Remove-Item Env:PYTHONPYCACHEPREFIX"),
            hook.index(wheel_command),
        )
        self.assertIn("$env:PYTHONPYCACHEPREFIX=$PreviousPythonPycCachePrefix", hook)

    def test_module_packaging_uses_short_windows_staging_paths(self) -> None:
        modules = Path(__file__).parents[1] / "modules"
        for prepare_path in sorted(modules.glob("*/src/prepare.ps1")):
            with self.subTest(module=prepare_path.parents[1].name):
                prepare = prepare_path.read_text(encoding="utf-8")
                self.assertIn("$PackageRoot = Join-Path $BuildRoot 'p'", prepare)
                self.assertNotIn("Join-Path $BuildRoot 'package'", prepare)
                self.assertNotIn("Join-Path $BuildRoot 'rehydrate'", prepare)
                self.assertNotIn("$Rehydrate", prepare)

    def test_module_packaging_skips_sealing_and_archive_tests(self) -> None:
        modules = Path(__file__).parents[1] / "modules"
        hash_command = "Get-" "FileHash"
        seal_manifest = "payload." + "sha" + "256.json"
        payload_command = "verify-" "payload"
        payload_function = "Test-" "Payload"
        for prepare_path in sorted(modules.glob("*/src/prepare.ps1")):
            with self.subTest(module=prepare_path.parents[1].name):
                prepare = prepare_path.read_text(encoding="utf-8")
                self.assertIn("function Remove-DirectoryWithRetry", prepare)
                self.assertNotIn(hash_command, prepare)
                self.assertNotIn(seal_manifest, prepare)
                self.assertNotIn(payload_command, prepare)
                self.assertNotIn("$SevenZip t $Archive", prepare)
                self.assertNotIn("$Rehydrate", prepare)

        for runtime_path in sorted(modules.glob("*/lib/runtime.ps1")):
            with self.subTest(module=runtime_path.parents[1].name):
                runtime = runtime_path.read_text(encoding="utf-8")
                self.assertNotIn(payload_function, runtime)
                self.assertNotIn(hash_command, runtime)
                self.assertNotIn(seal_manifest, runtime)

        for control_path in sorted(modules.glob("*/control.ps1")):
            with self.subTest(module=control_path.parent.name):
                control = control_path.read_text(encoding="utf-8")
                self.assertNotIn(payload_function, control)
                self.assertNotIn(payload_command, control)

    def test_stack_commands_reject_the_online_source_tree(self) -> None:
        root = Path(__file__).parents[1]
        control = (root / "stack" / "control.ps1").read_text(encoding="utf-8")
        self.assertIn("function Assert-Deployment", control)
        self.assertIn("(Join-Path $Root 'PREPARE-STACK.bat')", control)
        self.assertIn("'install'{Assert-Deployment", control)
        self.assertIn("'start'{Assert-Deployment", control)
        self.assertIn("'status'{Assert-Deployment", control)
        self.assertIn("'verify'{Assert-Deployment", control)
        self.assertIn("not from the source tree", control)

    def test_orchestrator_requires_all_prepared_module_archives(self) -> None:
        prepare = (
            Path(__file__).parents[1] / "stack" / "prepare.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("$MissingArchives = @()", prepare)
        self.assertIn("$Archive = Join-Path $PreparedRoot", prepare)
        self.assertIn("CHECK-SOURCES.bat checks downloads only", prepare)

    def test_module_archives_target_legacy_7zip_decoders(self) -> None:
        modules = Path(__file__).parents[1] / "modules"
        for prepare_path in sorted(modules.glob("*/src/prepare.ps1")):
            with self.subTest(module=prepare_path.parents[1].name):
                prepare = prepare_path.read_text(encoding="utf-8")
                self.assertIn("-myv=1900", prepare)

    def test_stack_bundles_and_drives_the_matching_extractor(self) -> None:
        root = Path(__file__).parents[1]
        prepare = (root / "stack" / "prepare.ps1").read_text(encoding="utf-8")
        extract = (root / "stack" / "extract.ps1").read_text(encoding="utf-8")
        launcher = (root / "EXTRACT-MODULES.bat").read_text(encoding="utf-8")
        hash_command = "Get-" "FileHash"
        payload_command = "verify-" "payload"

        self.assertIn("$SevenZipVersion = '26.02'", prepare)
        self.assertNotIn(hash_command, prepare)
        self.assertIn("module-archives.json", prepare)
        self.assertIn("7zip-LICENSE.txt", prepare)
        self.assertIn("tools\\7za.exe", extract)
        self.assertNotIn(hash_command, extract)
        self.assertNotIn("archive " "test", extract.lower())
        self.assertIn("@('x','-y','-aoa','-bd'", extract)
        self.assertNotIn(payload_command, extract)
        self.assertIn("Refusing to extract module payloads into the online source tree", extract)
        self.assertNotIn("Tee-Object -FilePath $Log -Append", extract)
        self.assertIn("Add-Content -LiteralPath $Log", extract)
        self.assertIn("stack\\extract.ps1", launcher)

    def test_stack_commands_write_a_transcript(self) -> None:
        control = (
            Path(__file__).parents[1] / "stack" / "control.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("Start-Transcript -Path $StackLog -Append -Force", control)
        self.assertIn("Stop-Transcript", control)


if __name__ == "__main__":
    unittest.main()
