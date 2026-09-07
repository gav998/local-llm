#!/usr/bin/env python3
"""Small fail-closed process supervisor used by the portable Windows launcher."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any


CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def process_identity(pid: int) -> str:
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


def terminate_tree(process: subprocess.Popen[Any], timeout: float) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.terminate()
        process.wait(timeout=timeout)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    else:
        process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def run_graceful(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    if not command:
        return
    try:
        subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True, type=Path)
    args = parser.parse_args()

    spec = load_json(args.spec)
    metadata_path = Path(spec["metadata"])
    stop_path = Path(spec["stop_file"])
    log_path = Path(spec["log"])
    cwd = Path(spec["cwd"])
    command = [str(item) for item in spec["command"]]
    graceful = [str(item) for item in spec.get("graceful", [])]
    env = {str(key): str(value) for key, value in spec["environment"].items()}
    token = str(spec["token"])

    log_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    stop_path.unlink(missing_ok=True)

    with log_path.open("ab", buffering=0) as log_stream:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NEW_PROCESS_GROUP,
            )
        except OSError as exc:
            message = f"Could not launch child: {exc}"
            log_stream.write((message + "\n").encode("utf-8", errors="replace"))
            atomic_json(
                metadata_path,
                {
                    "schema": 1,
                    "name": spec["name"],
                    "token": token,
                    "supervisor_pid": os.getpid(),
                    "supervisor_identity": process_identity(os.getpid()),
                    "child_pid": 0,
                    "child_identity": "",
                    "started_at": time.time(),
                    "stopped_at": time.time(),
                    "state": "stopped",
                    "error": message,
                },
            )
            return 1
        started_at = time.time()
        supervisor_identity = process_identity(os.getpid())
        child_identity = process_identity(process.pid)
        if not supervisor_identity or not child_identity:
            terminate_tree(process, 2)
            atomic_json(
                metadata_path,
                {
                    "schema": 1,
                    "name": spec["name"],
                    "token": token,
                    "supervisor_pid": os.getpid(),
                    "supervisor_identity": supervisor_identity,
                    "child_pid": process.pid,
                    "child_identity": child_identity,
                    "started_at": started_at,
                    "stopped_at": time.time(),
                    "state": "stopped",
                    "error": "Could not obtain stable process identities",
                },
            )
            return 1
        atomic_json(
            metadata_path,
            {
                "schema": 1,
                "name": spec["name"],
                "token": token,
                "supervisor_pid": os.getpid(),
                "supervisor_identity": supervisor_identity,
                "child_pid": process.pid,
                "child_identity": child_identity,
                "started_at": started_at,
                "command": command,
                "log": str(log_path),
                "state": "running",
            },
        )

        requested_stop = False
        while process.poll() is None:
            if stop_path.is_file():
                requested_stop = True
                run_graceful(graceful, cwd, env)
                terminate_tree(process, float(spec.get("stop_timeout", 20)))
                break
            time.sleep(0.4)

        return_code = process.poll()
        atomic_json(
            metadata_path,
            {
                "schema": 1,
                "name": spec["name"],
                "token": token,
                "supervisor_pid": os.getpid(),
                "supervisor_identity": supervisor_identity,
                "child_pid": process.pid,
                "child_identity": child_identity,
                "started_at": started_at,
                "stopped_at": time.time(),
                "return_code": return_code,
                "requested_stop": requested_stop,
                "command": command,
                "log": str(log_path),
                "state": "stopped",
            },
        )
    stop_path.unlink(missing_ok=True)
    return 0 if requested_stop or return_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
