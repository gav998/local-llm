#!/usr/bin/env python3
"""Configure, validate and control the portable local_llm Windows stack."""

from __future__ import annotations

import argparse
import configparser
import ctypes
import ctypes.wintypes
import hashlib
import json
import math
import os
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


CONTROL_VERSION = "2026.09.08.5"
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)

SERVICE_ORDER = (
    "mysql",
    "elasticsearch",
    "silo",
    "valkey",
    "embedding",
    "ocr",
    "chat",
    "ragflow-api",
    "task-executor",
    "caddy",
)
STOP_ORDER = tuple(reversed(SERVICE_ORDER))

DEFAULT_CONFIG = """# local_llm runtime settings. Relative paths are resolved below app\\.
# Stop the stack before editing ports, models or GPU placement.

[ports]
web = 9388
ragflow = 9380
mysql = 3306
elasticsearch = 1200
silo = 9000
silo_console = 9001
valkey = 6379
ocr = 9399
embedding = 6380
chat = 6381
caddy_admin = 2019

[models]
embedding = models/embed/Qwen3-Embedding-8B-Q4_K_M.gguf
chat = models/llm/Vikhr-Nemo-12B-Q4_K_M.gguf

[gpu]
# CUDA index for PaddleOCR. The target profile requires a GTX 1080 or newer.
ocr_index = 0
# llama.cpp Vulkan device numbering should be confirmed with: LOCAL-LLM.bat devices
embedding_gpu_ingestion = 1
embedding_gpu_chat = 0
chat_main_gpu = 1
chat_tensor_split = 0.20,0.80

[llama]
embedding_context = 2048
embedding_batch = 512
chat_context = 4096
chat_batch = 256
chat_parallel = 1

[memory]
mysql_buffer_mb = 768
elasticsearch_heap_mb = 2048
valkey_max_mb = 256

[runtime]
startup_timeout_seconds = 180
ocr_startup_timeout_seconds = 600
shutdown_timeout_seconds = 30
"""


class ControlError(RuntimeError):
    pass


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8", newline="\n")
    temporary.replace(path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ControlError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ControlError(f"Expected a JSON object in {path}")
    return value


def yaml_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def windows_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def tree_fingerprint(root: Path, exclusions: tuple[str, ...] = ()) -> tuple[str, int]:
    if root.is_symlink() or not root.is_dir():
        raise ControlError(f"Unsafe or missing tree root: {root}")
    normalized = tuple(
        item.replace("\\", "/").lstrip("/").casefold() for item in exclusions
    )
    rows: list[str] = []

    def walk_error(error: OSError) -> None:
        raise ControlError(f"Cannot read sealed tree {root}: {error}") from error

    for directory, dir_names, file_names in os.walk(
        root, followlinks=False, onerror=walk_error
    ):
        directory_path = Path(directory)
        for name in dir_names:
            child = directory_path / name
            attributes = getattr(child.lstat(), "st_file_attributes", 0)
            if child.is_symlink() or attributes & 0x400:
                raise ControlError(f"Reparse point in sealed tree: {child}")
        for name in file_names:
            path = directory_path / name
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
            if path.is_symlink() or attributes & 0x400:
                raise ControlError(f"Reparse point in sealed tree: {path}")
            relative = path.relative_to(root).as_posix()
            folded = relative.casefold()
            skip = any(
                folded.startswith(rule) if rule.endswith("/") else folded == rule
                for rule in normalized
            )
            if skip:
                continue
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            rows.append(f"{relative}\0{path.stat().st_size}\0{digest.hexdigest()}")
    rows.sort()
    body = ("\n".join(rows) + ("\n" if rows else "")).encode("utf-8")
    return hashlib.sha256(body).hexdigest(), len(rows)


def marker_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ControlError(f"Tree seal is missing: {path}")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key not in values:
            values[key] = value
    return values


def validate_tree_seal(
    root: Path,
    marker: Path,
    exclusions: tuple[str, ...] = (),
    expected_fingerprint: str = "",
) -> None:
    values = marker_values(marker)
    if expected_fingerprint and values.get("fingerprint") != expected_fingerprint:
        raise ControlError(f"Obsolete or unexpected tree fingerprint: {marker}")
    expected_hash = values.get("tree_sha256", "")
    expected_count = values.get("tree_file_count", "")
    actual_hash, actual_count = tree_fingerprint(root, exclusions)
    if actual_hash != expected_hash or str(actual_count) != expected_count:
        raise ControlError(f"Installed tree no longer matches its seal: {root}")


def parse_int(
    parser: configparser.ConfigParser,
    section: str,
    key: str,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = parser.getint(section, key)
    except (configparser.Error, ValueError) as exc:
        raise ControlError(f"Invalid integer [{section}] {key}") from exc
    if not minimum <= value <= maximum:
        raise ControlError(f"[{section}] {key}={value} is outside {minimum}..{maximum}")
    return value


@dataclass(frozen=True)
class Service:
    name: str
    command: list[str]
    cwd: Path
    environment: dict[str, str]
    health: Callable[[], bool]
    graceful: list[str]
    startup_timeout: int


class Controller:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.app = self.root / "app"
        self.config_dir = self.app / "config" / "runtime"
        self.data_dir = self.app / "data"
        self.control_dir = self.data_dir / "control"
        self.logs_dir = self.app / "logs"
        self.ini_path = self.config_dir / "local-llm.ini"
        self.secrets_path = self.config_dir / "secrets.json"
        self.install_marker = self.control_dir / "install.ok.json"
        self.supervisor = self.app / "config" / "project" / "local_llm_supervisor.py"
        self.rag_python = self.app / "runtime" / "python-rag" / "python.exe"
        if os.name != "nt" and not self.rag_python.exists():
            self.rag_python = Path(sys.executable)
        self.config: configparser.ConfigParser | None = None
        self.secret_values: dict[str, str] = {}

    @contextmanager
    def operation_lock(self):
        self.control_dir.mkdir(parents=True, exist_ok=True)
        lock = self.control_dir / "operation.lock"
        for attempt in range(2):
            try:
                lock.mkdir()
                break
            except FileExistsError as exc:
                try:
                    owner_data = load_json(lock / "owner.json")
                    owner_pid = int(owner_data.get("pid", 0))
                    owner_identity = str(owner_data.get("identity", ""))
                except (ControlError, TypeError, ValueError) as owner_error:
                    raise ControlError(
                        f"Invalid operation lock; inspect it before manual removal: {lock}"
                    ) from owner_error
                if (
                    attempt == 0
                    and owner_identity
                    and not self.process_alive(owner_pid, owner_identity)
                ):
                    (lock / "owner.json").unlink(missing_ok=True)
                    try:
                        lock.rmdir()
                    except OSError:
                        pass
                    continue
                raise ControlError(
                    "Another control operation may be running. If it was interrupted, "
                    f"confirm that no LOCAL-LLM command is active, then remove: {lock}"
                ) from exc
        owner = lock / "owner.json"
        try:
            atomic_json(
                owner,
                {
                    "pid": os.getpid(),
                    "identity": self.process_identity(os.getpid()),
                    "created_at": time.time(),
                },
            )
            yield
        finally:
            owner.unlink(missing_ok=True)
            try:
                lock.rmdir()
            except OSError:
                pass

    def require_payload(self) -> None:
        required = (
            self.app / "runtime" / "python-rag" / "python.exe",
            self.app / "runtime" / "python-ocr" / "python.exe",
            self.app / "ragflow" / "api" / "ragflow_server.py",
            self.app / "ragflow" / "rag" / "svr" / "task_executor.py",
            self.app / "web" / "index.html",
            self.app / "services" / "mysql" / "bin" / "mysqld.exe",
            self.app / "services" / "mysql" / "bin" / "mysql.exe",
            self.app / "services" / "mysql" / "bin" / "mysqladmin.exe",
            self.app / "services" / "elasticsearch" / "bin" / "elasticsearch.bat",
            self.app / "services" / "silo" / "silo.exe",
            self.app / "services" / "valkey" / "valkey-server.exe",
            self.app / "services" / "valkey" / "valkey-cli.exe",
            self.app / "services" / "caddy" / "caddy.exe",
            self.app / "services" / "ocr" / "ocr_job_gateway.py",
            self.app / "runtime" / "llama" / "llama-server.exe",
            self.supervisor,
        )
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise ControlError(
                "Incomplete installed payload:\n  " + "\n  ".join(missing)
            )

    def ensure_config(self) -> None:
        self.require_payload()
        for path in (
            self.config_dir,
            self.control_dir,
            self.logs_dir,
            self.app / "temp",
            self.app / "cache" / "python-bytecode",
            self.data_dir / "mysql",
            self.data_dir / "elasticsearch",
            self.logs_dir / "elasticsearch",
            self.data_dir / "silo",
            self.data_dir / "valkey",
            self.data_dir / "ocr-jobs",
            self.app / "models" / "embed",
            self.app / "models" / "llm",
        ):
            path.mkdir(parents=True, exist_ok=True)

        if not self.ini_path.exists():
            atomic_text(self.ini_path, DEFAULT_CONFIG)
        self.config = configparser.ConfigParser(interpolation=None)
        try:
            with self.ini_path.open("r", encoding="utf-8") as stream:
                self.config.read_file(stream)
        except (OSError, configparser.Error) as exc:
            raise ControlError(f"Invalid {self.ini_path}: {exc}") from exc
        self._validate_config()

        if not self.secrets_path.exists():
            alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

            def password(length: int) -> str:
                return "".join(secrets.choice(alphabet) for _ in range(length))

            atomic_json(
                self.secrets_path,
                {
                    "schema": 1,
                    "mysql_password": password(32),
                    "valkey_password": password(40),
                    "silo_user": "ragflowlocal",
                    "silo_password": password(48),
                    "ragflow_secret": secrets.token_hex(32),
                    "ocr_token": password(48),
                },
            )
            try:
                self.secrets_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
        self.secret_values = load_json(self.secrets_path)
        required_secrets = {
            "mysql_password",
            "valkey_password",
            "silo_user",
            "silo_password",
            "ragflow_secret",
            "ocr_token",
        }
        if not required_secrets.issubset(self.secret_values) or any(
            not isinstance(self.secret_values[key], str) or not self.secret_values[key]
            for key in required_secrets
        ):
            raise ControlError(f"Missing or invalid keys in {self.secrets_path}")

        self._write_mysql_config()
        self._write_elasticsearch_config()
        self._write_valkey_config()
        self._write_ragflow_config()
        self._write_caddy_config()
        atomic_json(
            self.config_dir / "location.json",
            {
                "schema": 1,
                "control_version": CONTROL_VERSION,
                "root": str(self.root),
                "configured_at": time.time(),
            },
        )

    def _validate_config(self) -> None:
        assert self.config is not None
        ports = {
            key: parse_int(self.config, "ports", key, 1, 65535)
            for key in (
                "web",
                "ragflow",
                "mysql",
                "elasticsearch",
                "silo",
                "silo_console",
                "valkey",
                "ocr",
                "embedding",
                "chat",
                "caddy_admin",
            )
        }
        duplicates = sorted(
            {port for port in ports.values() if list(ports.values()).count(port) > 1}
        )
        if duplicates:
            raise ControlError(f"Ports must be unique; duplicates: {duplicates}")
        parse_int(self.config, "gpu", "ocr_index", 0, 31)
        parse_int(self.config, "gpu", "embedding_gpu_ingestion", 0, 31)
        parse_int(self.config, "gpu", "embedding_gpu_chat", 0, 31)
        parse_int(self.config, "gpu", "chat_main_gpu", 0, 31)
        for key in (
            "embedding_context",
            "embedding_batch",
            "chat_context",
            "chat_batch",
            "chat_parallel",
        ):
            parse_int(self.config, "llama", key, 1, 1_048_576)
        for key in ("mysql_buffer_mb", "elasticsearch_heap_mb", "valkey_max_mb"):
            parse_int(self.config, "memory", key, 64, 32768)
        for key in (
            "startup_timeout_seconds",
            "ocr_startup_timeout_seconds",
            "shutdown_timeout_seconds",
        ):
            parse_int(self.config, "runtime", key, 5, 3600)
        split = self.config.get("gpu", "chat_tensor_split", fallback="")
        try:
            values = [float(part.strip()) for part in split.split(",")]
        except ValueError as exc:
            raise ControlError(
                "[gpu] chat_tensor_split must be comma-separated numbers"
            ) from exc
        if (
            len(values) < 2
            or any(not math.isfinite(value) or value < 0 for value in values)
            or sum(values) <= 0
        ):
            raise ControlError(
                "[gpu] chat_tensor_split requires at least two non-negative weights"
            )
        self.model_path("embedding")
        self.model_path("chat")

    def port(self, key: str) -> int:
        assert self.config is not None
        return self.config.getint("ports", key)

    def integer(self, section: str, key: str) -> int:
        assert self.config is not None
        return self.config.getint(section, key)

    def model_path(self, key: str) -> Path:
        assert self.config is not None
        try:
            raw = self.config.get("models", key).strip()
        except configparser.Error as exc:
            raise ControlError(f"Missing [models] {key}") from exc
        if not raw:
            raise ControlError(f"[models] {key} must not be empty")
        path = Path(raw)
        if not path.is_absolute():
            path = self.app / path
        resolved = path.resolve()
        if not resolved.is_relative_to(self.app):
            raise ControlError(
                f"[models] {key} must resolve inside the portable app directory"
            )
        return resolved

    def _write_mysql_config(self) -> None:
        path = self.config_dir / "mysql.ini"
        base = windows_path(self.app / "services" / "mysql")
        data = windows_path(self.data_dir / "mysql")
        logs = windows_path(self.logs_dir)
        content = f"""[client]
protocol=tcp
host=127.0.0.1
port={self.port("mysql")}
user=root
password={self.secret_values["mysql_password"]}

[mysqld]
basedir={base}
datadir={data}
port={self.port("mysql")}
bind-address=127.0.0.1
mysqlx=OFF
skip-name-resolve
skip-log-bin
character-set-server=utf8mb4
collation-server=utf8mb4_unicode_ci
lower_case_table_names=1
max_connections=100
max_allowed_packet=1073741824
innodb_buffer_pool_size={self.integer("memory", "mysql_buffer_mb")}M
pid-file={data}/mysql.pid
log-error={logs}/mysql-error.log
"""
        atomic_text(path, content)

    def _write_elasticsearch_config(self) -> None:
        source = self.app / "services" / "elasticsearch" / "config"
        target = self.config_dir / "elasticsearch"
        target.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            destination = target / item.name
            if item.is_file() and item.name != "elasticsearch.yml":
                shutil.copy2(item, destination)
        content = f"""cluster.name: local-llm
node.name: local-llm-node-1
path.data: {json.dumps(windows_path(self.data_dir / "elasticsearch"))}
path.logs: {json.dumps(windows_path(self.logs_dir / "elasticsearch"))}
network.host: 127.0.0.1
http.port: {self.port("elasticsearch")}
discovery.type: single-node
xpack.security.enabled: false
xpack.security.enrollment.enabled: false
ingest.geoip.downloader.enabled: false
"""
        atomic_text(target / "elasticsearch.yml", content)

    def _write_valkey_config(self) -> None:
        content = f"""bind 127.0.0.1
protected-mode yes
port {self.port("valkey")}
timeout 0
tcp-keepalive 300
requirepass {self.secret_values["valkey_password"]}
maxmemory {self.integer("memory", "valkey_max_mb")}mb
maxmemory-policy volatile-lru
dir \"{windows_path(self.data_dir / "valkey")}\"
dbfilename dump.rdb
appendonly yes
appendfilename \"appendonly.aof\"
"""
        atomic_text(self.config_dir / "valkey.conf", content)

    def _write_ragflow_config(self) -> None:
        chat_model = self.model_path("chat").stem
        embedding_model = self.model_path("embedding").stem
        content = f"""ragflow:
  host: '127.0.0.1'
  http_port: {self.port("ragflow")}
  secret_key: {yaml_string(self.secret_values["ragflow_secret"])}
mysql:
  name: 'rag_flow'
  user: 'root'
  password: {yaml_string(self.secret_values["mysql_password"])}
  host: '127.0.0.1'
  port: {self.port("mysql")}
  max_connections: 100
  stale_timeout: 300
  max_allowed_packet: 1073741824
minio:
  user: {yaml_string(self.secret_values["silo_user"])}
  password: {yaml_string(self.secret_values["silo_password"])}
  host: '127.0.0.1:{self.port("silo")}'
  bucket: ''
  prefix_path: ''
es:
  hosts: 'http://127.0.0.1:{self.port("elasticsearch")}'
  verify_certs: false
redis:
  db: 1
  username: ''
  password: {yaml_string(self.secret_values["valkey_password"])}
  host: '127.0.0.1:{self.port("valkey")}'
user_default_llm:
  factory: 'OpenAI-API-Compatible'
  api_key: 'local-no-auth'
  base_url: 'http://127.0.0.1:{self.port("chat")}/v1'
  default_models:
    chat_model:
      name: {yaml_string(chat_model)}
      factory: 'OpenAI-API-Compatible'
      api_key: 'local-no-auth'
      base_url: 'http://127.0.0.1:{self.port("chat")}/v1'
    embedding_model:
      name: {yaml_string(embedding_model)}
      factory: 'OpenAI-API-Compatible'
      api_key: 'local-no-auth'
      base_url: 'http://127.0.0.1:{self.port("embedding")}/v1'
authentication:
  disable_password_login: false
  client:
    switch: false
"""
        atomic_text(self.app / "ragflow" / "conf" / "local.service_conf.yaml", content)

    def _write_caddy_config(self) -> None:
        content = f"""{{
    admin 127.0.0.1:{self.port("caddy_admin")}
    auto_https off
}}

http://127.0.0.1:{self.port("web")} {{
    root * {json.dumps(windows_path(self.app / "web"))}
    encode gzip

    handle /v1/* {{
        reverse_proxy 127.0.0.1:{self.port("ragflow")}
    }}
    handle /api/* {{
        reverse_proxy 127.0.0.1:{self.port("ragflow")}
    }}
    handle {{
        try_files {{path}} /index.html
        file_server
    }}
}}
"""
        atomic_text(self.config_dir / "Caddyfile", content)

    def portable_environment(self) -> dict[str, str]:
        env = {
            "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
            "WINDIR": os.environ.get("WINDIR", r"C:\Windows"),
            "COMSPEC": os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"),
            "PATH": os.pathsep.join(
                (
                    str(self.app / "runtime" / "shared-dll"),
                    str(self.app / "runtime" / "python-rag"),
                    str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"),
                    os.environ.get("SystemRoot", r"C:\Windows"),
                    str(
                        Path(os.environ.get("SystemRoot", r"C:\Windows"))
                        / "System32"
                        / "Wbem"
                    ),
                )
            ),
            "HOME": str(self.data_dir / "profile"),
            "USERPROFILE": str(self.data_dir / "profile"),
            "APPDATA": str(self.data_dir / "profile" / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(self.data_dir / "profile" / "AppData" / "Local"),
            "TEMP": str(self.app / "temp"),
            "TMP": str(self.app / "temp"),
            "XDG_CACHE_HOME": str(self.app / "cache"),
            "XDG_CONFIG_HOME": str(self.config_dir / "xdg"),
            "XDG_DATA_HOME": str(self.data_dir / "xdg"),
            "HF_HOME": str(self.app / "cache" / "huggingface"),
            "HUGGINGFACE_HUB_CACHE": str(self.app / "cache" / "huggingface" / "hub"),
            "TRANSFORMERS_CACHE": str(
                self.app / "cache" / "huggingface" / "transformers"
            ),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PADDLE_HOME": str(self.app / "cache" / "paddle"),
            "PADDLE_PDX_CACHE_HOME": str(self.app / "cache" / "paddlex"),
            "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "1",
            "PADDLE_PDX_DISABLE_DEVICE_FALLBACK": "1",
            "PADDLE_PDX_SERVING_SERIAL_PIPELINE_CALLS": "1",
            "NLTK_DATA": str(self.data_dir / "nltk"),
            "TIKTOKEN_CACHE_DIR": str(self.app / "ragflow"),
            "CUDA_CACHE_PATH": str(self.app / "cache" / "nvidia"),
            "PYTHONPYCACHEPREFIX": str(self.app / "cache" / "python-bytecode"),
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
            "PYTHONHOME": "",
            "PYTHONPATH": "",
            "VIRTUAL_ENV": "",
            "CONDA_PREFIX": "",
            "JAVA_TOOL_OPTIONS": "",
            "_JAVA_OPTIONS": "",
            "JDK_JAVA_OPTIONS": "",
            "CLASSPATH": "",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "RAG_PROJECT_BASE": str(self.app / "ragflow"),
            "DOC_ENGINE": "elasticsearch",
            "STORAGE_IMPL": "MINIO",
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
            "PADDLEOCR_BASE_URL": f"http://127.0.0.1:{self.port('ocr')}",
            "PADDLEOCR_API_URL": f"http://127.0.0.1:{self.port('ocr')}",
            "PADDLEOCR_ACCESS_TOKEN": self.secret_values["ocr_token"],
            "PADDLEOCR_ALGORITHM": "PP-StructureV3",
            "LOCAL_OCR_TOKEN": self.secret_values["ocr_token"],
            "LOCAL_OCR_GPU_INDEX": str(self.integer("gpu", "ocr_index")),
            "TIKA_SERVER_JAR": (self.app / "ragflow" / "tika-server-standard-3.3.0.jar")
            .resolve()
            .as_uri(),
            "REGISTER_ENABLED": "1",
            "SANDBOX_ENABLED": "0",
        }
        for key in (
            "OS",
            "SystemDrive",
            "PATHEXT",
            "PROCESSOR_ARCHITECTURE",
            "PROCESSOR_IDENTIFIER",
            "NUMBER_OF_PROCESSORS",
        ):
            if key in os.environ:
                env[key] = os.environ[key]
        Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
        Path(env["APPDATA"]).mkdir(parents=True, exist_ok=True)
        Path(env["LOCALAPPDATA"]).mkdir(parents=True, exist_ok=True)
        Path(env["TEMP"]).mkdir(parents=True, exist_ok=True)
        return env

    def _http_health(self, url: str, expected: tuple[int, ...] = (200,)) -> bool:
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "local-llm-control"}
            )
            with urllib.request.urlopen(request, timeout=4) as response:
                return response.status in expected
        except (OSError, urllib.error.URLError):
            return False

    def _tcp_health(self, port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return True
        except OSError:
            return False

    def _valkey_health(self) -> bool:
        executable = self.app / "services" / "valkey" / "valkey-cli.exe"
        env = self.portable_environment()
        env["VALKEYCLI_AUTH"] = self.secret_values["valkey_password"]
        try:
            result = subprocess.run(
                [
                    str(executable),
                    "-h",
                    "127.0.0.1",
                    "-p",
                    str(self.port("valkey")),
                    "PING",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=CREATE_NO_WINDOW,
            )
            return result.returncode == 0 and "PONG" in result.stdout
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _mysql_health(self) -> bool:
        executable = self.app / "services" / "mysql" / "bin" / "mysqladmin.exe"
        common = [
            str(executable),
            "--protocol=TCP",
            "--host=127.0.0.1",
            "--port",
            str(self.port("mysql")),
            "--user=root",
        ]
        commands = (
            [
                str(executable),
                f"--defaults-file={self.config_dir / 'mysql.ini'}",
                "ping",
            ],
            common + ["ping"],
        )
        for command in commands:
            try:
                result = subprocess.run(
                    command,
                    env=self.portable_environment(),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    creationflags=CREATE_NO_WINDOW,
                )
                if result.returncode == 0:
                    return True
            except (OSError, subprocess.TimeoutExpired):
                continue
        return False

    def services(self, mode: str) -> dict[str, Service]:
        env = self.portable_environment()
        startup = self.integer("runtime", "startup_timeout_seconds")
        shutdown = self.integer("runtime", "shutdown_timeout_seconds")
        mysql_ini = self.config_dir / "mysql.ini"
        mysqladmin = self.app / "services" / "mysql" / "bin" / "mysqladmin.exe"
        valkey_cli = self.app / "services" / "valkey" / "valkey-cli.exe"
        caddy = self.app / "services" / "caddy" / "caddy.exe"
        comspec = env["COMSPEC"]

        es_env = dict(env)
        es_env["ES_PATH_CONF"] = str(self.config_dir / "elasticsearch")
        es_env["ES_JAVA_HOME"] = str(self.app / "services" / "elasticsearch" / "jdk")
        es_env["JAVA_HOME"] = es_env["ES_JAVA_HOME"]
        heap = self.integer("memory", "elasticsearch_heap_mb")
        es_env["ES_JAVA_OPTS"] = f"-Xms{heap}m -Xmx{heap}m -Dlog4j2.disable.jmx=true"
        es_wrapper = self.config_dir / "start-elasticsearch.bat"
        atomic_text(
            es_wrapper,
            "@echo off\r\n"
            + f'call "{self.app / "services" / "elasticsearch" / "bin" / "elasticsearch.bat"}"\r\n'
            + "exit /b %ERRORLEVEL%\r\n",
        )

        silo_env = dict(env)
        silo_env["MINIO_ROOT_USER"] = self.secret_values["silo_user"]
        silo_env["MINIO_ROOT_PASSWORD"] = self.secret_values["silo_password"]
        silo_env["MINIO_BROWSER"] = "off"
        silo_env["MINIO_UPDATE"] = "off"

        valkey_env = dict(env)
        valkey_env["VALKEYCLI_AUTH"] = self.secret_values["valkey_password"]

        rag_env = dict(env)
        ragflow_dir = self.app / "ragflow"
        ocr_env = dict(env)
        llama = self.app / "runtime" / "llama" / "llama-server.exe"
        embedding = self.model_path("embedding")
        chat = self.model_path("chat")
        embedding_gpu_key = (
            "embedding_gpu_ingestion" if mode == "ingestion" else "embedding_gpu_chat"
        )

        result = {
            "mysql": Service(
                "mysql",
                [
                    str(self.app / "services" / "mysql" / "bin" / "mysqld.exe"),
                    f"--defaults-file={mysql_ini}",
                ],
                self.app / "services" / "mysql",
                env,
                self._mysql_health,
                [str(mysqladmin), f"--defaults-file={mysql_ini}", "shutdown"],
                startup,
            ),
            "elasticsearch": Service(
                "elasticsearch",
                [comspec, "/d", "/c", str(es_wrapper)],
                self.app / "services" / "elasticsearch",
                es_env,
                lambda: self._http_health(
                    f"http://127.0.0.1:{self.port('elasticsearch')}/"
                ),
                [],
                startup,
            ),
            "silo": Service(
                "silo",
                [
                    str(self.app / "services" / "silo" / "silo.exe"),
                    "server",
                    str(self.data_dir / "silo"),
                    "--address",
                    f"127.0.0.1:{self.port('silo')}",
                    "--console-address",
                    f"127.0.0.1:{self.port('silo_console')}",
                ],
                self.app / "services" / "silo",
                silo_env,
                lambda: self._http_health(
                    f"http://127.0.0.1:{self.port('silo')}/minio/health/ready"
                ),
                [],
                startup,
            ),
            "valkey": Service(
                "valkey",
                [
                    str(self.app / "services" / "valkey" / "valkey-server.exe"),
                    str(self.config_dir / "valkey.conf"),
                ],
                self.app / "services" / "valkey",
                valkey_env,
                self._valkey_health,
                [
                    str(valkey_cli),
                    "-h",
                    "127.0.0.1",
                    "-p",
                    str(self.port("valkey")),
                    "SHUTDOWN",
                    "SAVE",
                ],
                startup,
            ),
            "embedding": Service(
                "embedding",
                [
                    str(llama),
                    "--model",
                    str(embedding),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self.port("embedding")),
                    "--embedding",
                    "--pooling",
                    "last",
                    "--n-gpu-layers",
                    "999",
                    "--split-mode",
                    "none",
                    "--main-gpu",
                    str(self.integer("gpu", embedding_gpu_key)),
                    "--ctx-size",
                    str(self.integer("llama", "embedding_context")),
                    "--batch-size",
                    str(self.integer("llama", "embedding_batch")),
                    "--no-webui",
                ],
                self.app / "runtime" / "llama",
                env,
                lambda: self._http_health(
                    f"http://127.0.0.1:{self.port('embedding')}/health"
                ),
                [],
                startup,
            ),
            "ocr": Service(
                "ocr",
                [
                    str(self.app / "runtime" / "python-ocr" / "python.exe"),
                    str(self.app / "services" / "ocr" / "ocr_job_gateway.py"),
                    "--config",
                    str(self.app / "config" / "pp-structure-v3-8gb.yaml"),
                    "--model-root",
                    str(self.app / "cache" / "paddlex" / "official_models"),
                    "--jobs-root",
                    str(self.data_dir / "ocr-jobs"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self.port("ocr")),
                ],
                self.app / "services" / "ocr",
                ocr_env,
                lambda: self._http_health(
                    f"http://127.0.0.1:{self.port('ocr')}/health"
                ),
                [],
                self.integer("runtime", "ocr_startup_timeout_seconds"),
            ),
            "chat": Service(
                "chat",
                [
                    str(llama),
                    "--model",
                    str(chat),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self.port("chat")),
                    "--n-gpu-layers",
                    "999",
                    "--split-mode",
                    "layer",
                    "--main-gpu",
                    str(self.integer("gpu", "chat_main_gpu")),
                    "--tensor-split",
                    self.config.get("gpu", "chat_tensor_split"),
                    "--ctx-size",
                    str(self.integer("llama", "chat_context")),
                    "--batch-size",
                    str(self.integer("llama", "chat_batch")),
                    "--parallel",
                    str(self.integer("llama", "chat_parallel")),
                    "--no-webui",
                ],
                self.app / "runtime" / "llama",
                env,
                lambda: self._http_health(
                    f"http://127.0.0.1:{self.port('chat')}/health"
                ),
                [],
                startup,
            ),
            "ragflow-api": Service(
                "ragflow-api",
                [str(self.rag_python), str(ragflow_dir / "api" / "ragflow_server.py")],
                ragflow_dir,
                rag_env,
                lambda: self._http_health(
                    f"http://127.0.0.1:{self.port('ragflow')}/api/v1/system/healthz"
                ),
                [],
                startup,
            ),
            "task-executor": Service(
                "task-executor",
                [
                    str(self.rag_python),
                    str(ragflow_dir / "rag" / "svr" / "task_executor.py"),
                    "-i",
                    f"{socket.gethostname()[:24]}_0",
                    "-t",
                    "common",
                ],
                ragflow_dir,
                rag_env,
                lambda: True,
                [],
                startup,
            ),
            "caddy": Service(
                "caddy",
                [
                    str(caddy),
                    "run",
                    "--config",
                    str(self.config_dir / "Caddyfile"),
                    "--adapter",
                    "caddyfile",
                ],
                self.app / "services" / "caddy",
                env,
                lambda: self._http_health(f"http://127.0.0.1:{self.port('web')}/"),
                [
                    str(caddy),
                    "stop",
                    "--address",
                    f"127.0.0.1:{self.port('caddy_admin')}",
                ],
                startup,
            ),
        }
        for service in result.values():
            service.environment["LOCAL_LLM_SHUTDOWN_TIMEOUT"] = str(shutdown)
        return result

    def metadata_path(self, name: str) -> Path:
        return self.control_dir / f"{name}.json"

    def stop_path(self, name: str) -> Path:
        return self.control_dir / f"{name}.stop"

    @staticmethod
    def process_identity(pid: int) -> str:
        if pid <= 0:
            return ""
        if os.name != "nt":
            try:
                return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[21]
            except (OSError, IndexError):
                return ""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return ""
        creation = ctypes.wintypes.FILETIME()
        exit_time = ctypes.wintypes.FILETIME()
        kernel = ctypes.wintypes.FILETIME()
        user = ctypes.wintypes.FILETIME()
        try:
            if not ctypes.windll.kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return ""
            return str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

    @classmethod
    def process_alive(cls, pid: int, expected_identity: str = "") -> bool:
        if pid <= 0:
            return False
        if os.name != "nt":
            try:
                os.kill(pid, 0)
            except OSError:
                return False
            return (
                not expected_identity or cls.process_identity(pid) == expected_identity
            )
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid
        )
        if not handle:
            return False
        try:
            alive = ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 0x102
            return alive and (
                not expected_identity or cls.process_identity(pid) == expected_identity
            )
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

    def service_running(self, name: str) -> bool:
        state, _ = self.service_process_state(name)
        return state == "running"

    def service_process_state(self, name: str) -> tuple[str, dict[str, Any]]:
        metadata = self.metadata_path(name)
        if not metadata.is_file():
            return "stopped", {}
        try:
            value = load_json(metadata)
            supervisor_alive = self.process_alive(
                int(value["supervisor_pid"]), str(value["supervisor_identity"])
            )
            child_alive = self.process_alive(
                int(value["child_pid"]), str(value["child_identity"])
            )
        except (ControlError, KeyError, TypeError, ValueError):
            return "invalid", {}
        if value.get("state") == "running" and supervisor_alive and child_alive:
            return "running", value
        if child_alive:
            return "orphaned", value
        if supervisor_alive:
            return "supervisor-only", value
        return "stopped", value

    def start_service(self, service: Service) -> None:
        process_state, _ = self.service_process_state(service.name)
        if process_state == "running":
            if service.health():
                print(f"[OK] {service.name}: already ready")
                return
            raise ControlError(
                f"{service.name} is running but unhealthy; see {self.logs_dir / (service.name + '.log')}"
            )
        if process_state in {"invalid", "orphaned", "supervisor-only"}:
            raise ControlError(
                f"{service.name} has {process_state} process metadata; run stop before retrying"
            )
        if not self._port_available_for(service.name):
            raise ControlError(
                f"A required port for {service.name} is already occupied by another process"
            )
        self.metadata_path(service.name).unlink(missing_ok=True)

        token = uuid.uuid4().hex
        spec_path = self.control_dir / f"{service.name}.{token}.spec.json"
        spec = {
            "schema": 1,
            "name": service.name,
            "token": token,
            "metadata": str(self.metadata_path(service.name)),
            "stop_file": str(self.stop_path(service.name)),
            "log": str(self.logs_dir / f"{service.name}.log"),
            "cwd": str(service.cwd),
            "command": service.command,
            "graceful": service.graceful,
            "environment": service.environment,
            "stop_timeout": self.integer("runtime", "shutdown_timeout_seconds"),
        }
        atomic_json(spec_path, spec)
        self.stop_path(service.name).unlink(missing_ok=True)
        flags = CREATE_NEW_PROCESS_GROUP | CREATE_NEW_CONSOLE
        startup_info = None
        if os.name == "nt":
            startup_info = subprocess.STARTUPINFO()
            startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup_info.wShowWindow = 0
        supervisor_process = subprocess.Popen(
            [str(self.rag_python), str(self.supervisor), "--spec", str(spec_path)],
            cwd=self.control_dir,
            env=service.environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=flags,
            startupinfo=startup_info,
        )
        try:
            deadline = time.monotonic() + service.startup_timeout
            while time.monotonic() < deadline:
                if self.metadata_path(service.name).is_file() and self.service_running(
                    service.name
                ):
                    if service.health():
                        if service.name == "task-executor":
                            time.sleep(2)
                            if not self.service_running(service.name):
                                raise ControlError(
                                    f"{service.name} exited during startup; see {self.logs_dir / (service.name + '.log')}"
                                )
                        print(f"[OK] {service.name}: ready")
                        return
                elif self.metadata_path(service.name).is_file():
                    value = load_json(self.metadata_path(service.name))
                    if value.get("token") == token and value.get("state") == "stopped":
                        raise ControlError(
                            f"{service.name} exited during startup; see {self.logs_dir / (service.name + '.log')}"
                        )
                if supervisor_process.poll() is not None:
                    raise ControlError(
                        f"Supervisor for {service.name} exited during startup; see {self.logs_dir / (service.name + '.log')}"
                    )
                time.sleep(1)
            self.stop_path(service.name).touch()
            stop_deadline = (
                time.monotonic()
                + self.integer("runtime", "shutdown_timeout_seconds")
                + 15
            )
            while time.monotonic() < stop_deadline and self.service_running(
                service.name
            ):
                time.sleep(0.5)
            raise ControlError(
                f"Timed out waiting for {service.name}; see {self.logs_dir / (service.name + '.log')}"
            )
        except (ControlError, KeyboardInterrupt):
            self.stop_path(service.name).touch()
            try:
                self.stop_service(service.name, quiet=True)
            except ControlError:
                pass
            raise
        finally:
            spec_path.unlink(missing_ok=True)

    def _port_available_for(self, name: str) -> bool:
        keys = {
            "mysql": ("mysql",),
            "elasticsearch": ("elasticsearch",),
            "silo": ("silo", "silo_console"),
            "valkey": ("valkey",),
            "embedding": ("embedding",),
            "ocr": ("ocr",),
            "chat": ("chat",),
            "ragflow-api": ("ragflow",),
            "caddy": ("web", "caddy_admin"),
            "task-executor": (),
        }[name]
        for key in keys:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                try:
                    sock.bind(("127.0.0.1", self.port(key)))
                except OSError:
                    return False
        return True

    def stop_service(self, name: str, quiet: bool = False) -> None:
        process_state, metadata = self.service_process_state(name)
        if process_state == "invalid":
            raise ControlError(
                f"Invalid process metadata for {name}: {self.metadata_path(name)}"
            )
        if process_state == "orphaned":
            self._terminate_orphan(name, metadata)
            print(f"[OK] {name}: stopped orphaned child")
            return
        if process_state == "stopped":
            if not quiet:
                print(f"[--] {name}: stopped")
            return
        self.stop_path(name).touch()
        deadline = (
            time.monotonic() + self.integer("runtime", "shutdown_timeout_seconds") + 15
        )
        while time.monotonic() < deadline:
            current_state, current_metadata = self.service_process_state(name)
            if current_state == "stopped":
                print(f"[OK] {name}: stopped")
                return
            if current_state == "orphaned":
                self._terminate_orphan(name, current_metadata)
                print(f"[OK] {name}: stopped orphaned child")
                return
            if current_state == "invalid":
                raise ControlError(
                    f"Process metadata became invalid for {name}: {self.metadata_path(name)}"
                )
            time.sleep(0.5)
        raise ControlError(
            f"Supervisor did not stop {name}; see {self.logs_dir / (name + '.log')}"
        )

    def _terminate_orphan(self, name: str, metadata: dict[str, Any]) -> None:
        try:
            pid = int(metadata["child_pid"])
            identity = str(metadata["child_identity"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ControlError(f"Cannot identify orphaned child for {name}") from exc
        if not identity or not self.process_alive(pid, identity):
            return
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
                creationflags=CREATE_NO_WINDOW,
            )
            if result.returncode != 0 and self.process_alive(pid, identity):
                raise ControlError(f"Could not terminate orphaned child PID {pid}")
        else:
            os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not self.process_alive(pid, identity):
                metadata["state"] = "stopped"
                metadata["stopped_at"] = time.time()
                metadata["recovered_orphan"] = True
                atomic_json(self.metadata_path(name), metadata)
                return
            time.sleep(0.2)
        raise ControlError(f"Orphaned child PID {pid} did not stop")

    def require_installed(self) -> None:
        if not self.install_marker.is_file():
            raise ControlError(
                "Installation is not finalized. Run LOCAL-LLM.bat install."
            )
        marker = load_json(self.install_marker)
        if (
            marker.get("control_version") != CONTROL_VERSION
            or marker.get("gpu_e2e") != "passed"
        ):
            raise ControlError(
                "Installation marker is obsolete or lacks the required GPU E2E gate; run install again."
            )

    def installation_ready(self) -> bool:
        try:
            self.require_installed()
        except (ControlError, OSError, ValueError):
            return False
        return True

    def initialize_mysql(self) -> None:
        data = self.data_dir / "mysql"
        if not (data / "mysql").is_dir():
            print("[STEP] Initialize portable MySQL data directory")
            command = [
                str(self.app / "services" / "mysql" / "bin" / "mysqld.exe"),
                f"--defaults-file={self.config_dir / 'mysql.ini'}",
                "--initialize-insecure",
            ]
            result = subprocess.run(
                command,
                cwd=self.app / "services" / "mysql",
                env=self.portable_environment(),
                timeout=300,
                check=False,
            )
            if result.returncode != 0 or not (data / "mysql").is_dir():
                raise ControlError(
                    f"MySQL initialization failed; see {self.logs_dir / 'mysql-error.log'}"
                )

        services = self.services("core")
        self.start_service(services["mysql"])
        try:
            mysql = self.app / "services" / "mysql" / "bin" / "mysql.exe"
            authenticated = [
                str(mysql),
                f"--defaults-file={self.config_dir / 'mysql.ini'}",
                "--batch",
                "--skip-column-names",
            ]
            probe = subprocess.run(
                authenticated + ["-e", "SELECT 1"],
                capture_output=True,
                timeout=20,
                check=False,
            )
            if probe.returncode != 0:
                bootstrap = [
                    str(mysql),
                    "--protocol=TCP",
                    "--host=127.0.0.1",
                    "--port",
                    str(self.port("mysql")),
                    "--user=root",
                    "--batch",
                ]
                escaped = self.secret_values["mysql_password"].replace("'", "''")
                sql = f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{escaped}'; CREATE DATABASE IF NOT EXISTS rag_flow CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
                result = subprocess.run(
                    bootstrap + ["-e", sql],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                if result.returncode != 0:
                    raise ControlError(
                        "Could not secure and create the local MySQL database"
                    )
            else:
                result = subprocess.run(
                    authenticated
                    + [
                        "-e",
                        "CREATE DATABASE IF NOT EXISTS rag_flow CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci",
                    ],
                    timeout=30,
                    check=False,
                )
                if result.returncode != 0:
                    raise ControlError("Could not create the local MySQL database")
        finally:
            self.stop_service("mysql", quiet=True)

    def verify_static(self, gpu: bool) -> None:
        print("[STEP] Verify installed runtimes and offline assets")
        self.verify_tree_seals()
        env = self.portable_environment()
        checks = [
            (
                [
                    str(self.rag_python),
                    "-c",
                    "import cv2, elasticsearch, quart, valkey; print('RAG runtime OK')",
                ],
                self.app,
            ),
            (
                [
                    str(self.app / "runtime" / "python-ocr" / "python.exe"),
                    "-c",
                    "import paddle,paddleocr,paddlex; print('OCR runtime OK')",
                ],
                self.app,
            ),
            (
                [
                    str(self.rag_python),
                    str(self.app / "config" / "project" / "prepare_ragflow_assets.py"),
                    "--ragflow-dir",
                    str(self.app / "ragflow"),
                    "--nltk-dir",
                    str(self.data_dir / "nltk"),
                    "--record",
                    str(self.config_dir / "ragflow-assets-verify.json"),
                    "--verify-only",
                ],
                self.app,
            ),
            (
                [
                    str(self.rag_python),
                    str(self.app / "config" / "project" / "verify_ragflow_runtime.py"),
                    "--ragflow-dir",
                    str(self.app / "ragflow"),
                ],
                self.app,
            ),
            (
                [
                    str(self.app / "runtime" / "python-ocr" / "python.exe"),
                    str(
                        self.app
                        / "services"
                        / "ocr"
                        / "test_ocr_job_gateway_contract.py"
                    ),
                ],
                self.app,
            ),
        ]
        log = self.logs_dir / "offline-verify.log"
        with log.open("w", encoding="utf-8") as stream:
            for command, cwd in checks:
                result = subprocess.run(
                    command,
                    cwd=cwd,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    timeout=300,
                    check=False,
                )
                if result.returncode != 0:
                    raise ControlError(
                        f"Offline runtime verification failed; see {log}"
                    )
        if gpu:
            self.verify_gpu()
        print("[OK] Installed payload verification passed")

    def verify_tree_seals(self) -> None:
        print(
            "[STEP] Verify final tree seals (this reads the complete installed payload)"
        )
        config = self.app / "config"
        mutable = (
            (
                self.app / "runtime" / "python-rag",
                config / "python-rag-tree.ok",
                (),
                f"tree-schema=1;project={CONTROL_VERSION};component=python-rag",
            ),
            (
                self.app / "runtime" / "python-ocr",
                config / "python-ocr-tree.ok",
                (),
                f"tree-schema=1;project={CONTROL_VERSION};component=python-ocr",
            ),
            (
                self.app / "ragflow",
                config / "ragflow-tree.ok",
                ("conf/local.service_conf.yaml", "logs/"),
                f"tree-schema=1;project={CONTROL_VERSION};component=ragflow",
            ),
        )
        for root, marker, exclusions, fingerprint in mutable:
            validate_tree_seal(root, marker, exclusions, fingerprint)

        immutable = (
            self.app / "tools" / "7zip",
            self.app / "cache" / "paddlex" / "official_models" / "PP-DocLayout-L",
            self.app / "cache" / "paddlex" / "official_models" / "PP-DocBlockLayout",
            self.app / "cache" / "paddlex" / "official_models" / "PP-OCRv6_medium_det",
            self.app
            / "cache"
            / "paddlex"
            / "official_models"
            / "eslav_PP-OCRv5_mobile_rec",
            self.app / "cache" / "paddlex" / "official_models" / "SLANet_plus",
            self.app / "services" / "ocr" / "font",
            self.app / "services" / "mysql",
            self.app / "services" / "elasticsearch",
            self.app / "services" / "silo",
            self.app / "services" / "valkey",
            self.app / "services" / "caddy",
            self.app / "runtime" / "llama",
        )
        for root in immutable:
            validate_tree_seal(
                root, root / ".local-llm-artifact.txt", (".local-llm-artifact.txt",)
            )
        validate_tree_seal(self.app / "web", config / "ragflow-web.ok")
        print("[OK] All final tree seals match")

    def verify_gpu(self) -> None:
        print("[STEP] Run strict CUDA/OCR/RAGFlow E2E (no CPU fallback)")
        env = self.portable_environment()
        work = self.app / "temp" / "offline-gpu-e2e"
        work.mkdir(parents=True, exist_ok=True)
        command = [
            str(self.rag_python),
            str(self.app / "services" / "ocr" / "ocr_ragflow_e2e.py"),
            "--ocr-python",
            str(self.app / "runtime" / "python-ocr" / "python.exe"),
            "--gateway-script",
            str(self.app / "services" / "ocr" / "ocr_job_gateway.py"),
            "--config",
            str(self.app / "config" / "pp-structure-v3-8gb.yaml"),
            "--model-root",
            str(self.app / "cache" / "paddlex" / "official_models"),
            "--font",
            str(self.app / "services" / "ocr" / "font" / "ttf" / "DejaVuSans.ttf"),
            "--ragflow-dir",
            str(self.app / "ragflow"),
            "--work-dir",
            str(work),
            "--record",
            str(self.config_dir / "ocr-gpu-smoke.json"),
            "--log",
            str(self.logs_dir / "offline-ocr-e2e-server.log"),
        ]
        log = self.logs_dir / "offline-gpu-e2e.log"
        with log.open("w", encoding="utf-8") as stream:
            result = subprocess.run(
                command,
                cwd=self.app,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=1800,
                check=False,
            )
        if result.returncode != 0:
            raise ControlError(f"Strict GPU E2E failed; see {log}")
        print("[OK] Strict GPU E2E passed")

    def finalize_install(self) -> None:
        self.ensure_config()
        running = [name for name in SERVICE_ORDER if self.service_running(name)]
        if running:
            raise ControlError(
                "Stop the stack before install verification; running: "
                + ", ".join(running)
            )
        self.verify_static(gpu=True)
        self.initialize_mysql()
        atomic_json(
            self.install_marker,
            {
                "schema": 1,
                "control_version": CONTROL_VERSION,
                "installed_at": time.time(),
                "root_at_install": str(self.root),
                "gpu_e2e": "passed",
            },
        )
        print("[OK] Offline installation finalized")

    def start(self, mode: str) -> None:
        self.ensure_config()
        self.require_installed()
        if mode not in {"core", "ingestion", "chat"}:
            raise ControlError("Mode must be core, ingestion or chat")
        services = self.services(mode)
        optional = {"embedding", "ocr", "chat", "task-executor"}
        wanted = {
            "core": set(),
            "ingestion": {"embedding", "ocr", "task-executor"},
            "chat": {"embedding", "chat"},
        }[mode]
        if mode in {"ingestion", "chat"} and not self.model_path("embedding").is_file():
            raise ControlError(
                f"Embedding GGUF not found: {self.model_path('embedding')}"
            )
        if mode == "chat" and not self.model_path("chat").is_file():
            raise ControlError(f"Chat GGUF not found: {self.model_path('chat')}")
        profile_path = self.control_dir / "active-profile.json"
        old_profile = ""
        if profile_path.is_file():
            try:
                old_profile = str(load_json(profile_path).get("profile", ""))
            except ControlError:
                old_profile = ""
        if old_profile and old_profile != mode:
            profile_path.unlink(missing_ok=True)
            self.stop_service("embedding", quiet=True)
        for name in reversed(SERVICE_ORDER):
            if name in optional and name not in wanted:
                self.stop_service(name, quiet=True)
        started: list[str] = []
        try:
            for name in SERVICE_ORDER:
                if name in optional and name not in wanted:
                    continue
                was_running = self.service_running(name)
                self.start_service(services[name])
                if not was_running:
                    started.append(name)
        except (
            ControlError,
            OSError,
            subprocess.SubprocessError,
            KeyboardInterrupt,
        ) as exc:
            profile_path.unlink(missing_ok=True)
            for name in reversed(started):
                try:
                    self.stop_service(name, quiet=True)
                except ControlError:
                    pass
            if isinstance(exc, KeyboardInterrupt):
                raise
            raise ControlError(
                f"Profile startup failed and newly started services were rolled back: {exc}"
            ) from exc
        atomic_json(
            self.control_dir / "active-profile.json",
            {"profile": mode, "started_at": time.time()},
        )
        print(f"[OK] Profile '{mode}' is ready: http://127.0.0.1:{self.port('web')}")

    def stop(self) -> None:
        try:
            self.ensure_config()
        except (ControlError, OSError) as exc:
            print(f"[WARN] Configuration is invalid; using safe stop timeout: {exc}")
            self.config = configparser.ConfigParser(interpolation=None)
            self.config.read_string(DEFAULT_CONFIG)
        errors: list[str] = []
        for name in STOP_ORDER:
            try:
                self.stop_service(name, quiet=True)
            except ControlError as exc:
                errors.append(str(exc))
        (self.control_dir / "active-profile.json").unlink(missing_ok=True)
        if errors:
            raise ControlError(
                "Some services did not stop cleanly:\n  " + "\n  ".join(errors)
            )
        print("[OK] local_llm is stopped")

    def status(self) -> int:
        self.ensure_config()
        services = self.services("chat")
        installed = self.installation_ready()
        profile = "none"
        profile_path = self.control_dir / "active-profile.json"
        if profile_path.is_file():
            try:
                profile = str(load_json(profile_path).get("profile", "unknown"))
            except ControlError:
                profile = "invalid"
        print(f"Installation: {'ready' if installed else 'not finalized'}")
        print(f"Profile     : {profile}")
        print(f"Web URL     : http://127.0.0.1:{self.port('web')}")
        print("\nSERVICE          PROCESS          HEALTH")
        print("---------------  ---------------  --------")
        unhealthy = False
        for name in SERVICE_ORDER:
            process_state, _ = self.service_process_state(name)
            running = process_state == "running"
            healthy = running and services[name].health()
            if process_state in {"invalid", "orphaned", "supervisor-only"} or (
                running and not healthy and name != "task-executor"
            ):
                unhealthy = True
            health = "ready" if healthy else ("n/a" if not running else "failed")
            print(f"{name:15}  {process_state:15}  {health}")
        return 1 if unhealthy else 0

    def show_config(self) -> None:
        self.ensure_config()
        print(f"Settings: {self.ini_path}")
        print(f"Secrets : {self.secrets_path} (do not share)")
        print(self.ini_path.read_text(encoding="utf-8"))

    def edit_config(self) -> None:
        self.ensure_config()
        running = [name for name in SERVICE_ORDER if self.service_running(name)]
        if running:
            raise ControlError(
                "Stop the stack before editing settings; running: " + ", ".join(running)
            )
        editor = (
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32"
            / "notepad.exe"
        )
        result = subprocess.run([str(editor), str(self.ini_path)], check=False)
        if result.returncode != 0:
            raise ControlError(f"Editor failed with exit code {result.returncode}")
        self.ensure_config()
        print("[OK] Configuration is valid and generated files were refreshed")

    def devices(self) -> None:
        self.ensure_config()
        executable = self.app / "runtime" / "llama" / "llama-server.exe"
        result = subprocess.run(
            [str(executable), "--list-devices"],
            cwd=executable.parent,
            env=self.portable_environment(),
            check=False,
        )
        if result.returncode != 0:
            raise ControlError(
                f"llama.cpp device probe failed with exit code {result.returncode}"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    version = sub.add_parser("version")
    version.add_argument("--expect")
    sub.add_parser("install-finalize")
    start = sub.add_parser("start")
    start.add_argument(
        "mode", nargs="?", default="core", choices=("core", "ingestion", "chat")
    )
    sub.add_parser("stop")
    sub.add_parser("status")
    verify = sub.add_parser("verify")
    verify.add_argument("--gpu", action="store_true")
    config = sub.add_parser("config")
    config.add_argument("action", nargs="?", default="show", choices=("show", "edit"))
    sub.add_parser("devices")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    controller = Controller(args.root)
    try:
        if args.command == "version":
            if args.expect and args.expect != CONTROL_VERSION:
                raise ControlError(
                    f"Controller {CONTROL_VERSION} does not match launcher {args.expect}"
                )
            print(CONTROL_VERSION)
        elif args.command == "install-finalize":
            with controller.operation_lock():
                controller.finalize_install()
        elif args.command == "start":
            with controller.operation_lock():
                controller.start(args.mode)
        elif args.command == "stop":
            with controller.operation_lock():
                controller.stop()
        elif args.command == "status":
            return controller.status()
        elif args.command == "verify":
            with controller.operation_lock():
                controller.ensure_config()
                controller.verify_static(gpu=args.gpu)
        elif args.command == "config":
            if args.action == "edit":
                with controller.operation_lock():
                    controller.edit_config()
            else:
                controller.show_config()
        elif args.command == "devices":
            controller.devices()
        return 0
    except (ControlError, OSError, subprocess.SubprocessError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("[ERROR] Operation interrupted by the user", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
