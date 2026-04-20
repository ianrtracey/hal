#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shlex
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VAR = ROOT / "var"
LOG_DIR = VAR / "log"
APP_LOG = LOG_DIR / "hal.log"
DB_PATH = Path(os.environ.get("HAL_DB_PATH", VAR / "hal.sqlite3"))
RESTART_SIGNAL = Path(os.environ.get("HAL_RESTART_SIGNAL_PATH", VAR / "restart.request"))

HOST = os.environ.get("HAL_HOST", "127.0.0.1")
PORT = int(os.environ.get("HAL_PORT", "8000"))
HEALTH_URL = os.environ.get("HAL_HEALTH_URL", f"http://{HOST}:{PORT}/health")
HEALTH_TIMEOUT = float(os.environ.get("HAL_HEALTH_TIMEOUT", "20"))
ROLLBACK_ON_FAILED_HEALTH = os.environ.get("HAL_ROLLBACK_ON_FAILED_HEALTH", "1") != "0"
APP_CMD = shlex.split(
    os.environ.get(
        "HAL_APP_CMD",
        f"{sys.executable} -m uvicorn hal.app:app --host {HOST} --port {PORT}",
    )
)

stop_requested = False


def log_error(source: str, severity: str, message: str, raw: dict | None = None) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS error_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                traceback TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO error_logs (source, severity, message, traceback, raw_json, created_at)
            VALUES (?, ?, ?, NULL, ?, datetime('now'))
            """,
            (source, severity, message, json.dumps(raw, sort_keys=True) if raw else None),
        )


def append_log(line: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with APP_LOG.open("a") as handle:
        handle.write(f"\n[supervisor] {line}\n")


def start_app() -> subprocess.Popen:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    append_log(f"starting: {' '.join(APP_CMD)}")
    log_handle = APP_LOG.open("a")
    return subprocess.Popen(
        APP_CMD,
        cwd=ROOT,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
        text=True,
    )


def stop_app(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def health_ok(proc: subprocess.Popen) -> bool:
    deadline = time.monotonic() + HEALTH_TIMEOUT
    while time.monotonic() < deadline and not stop_requested:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


def revert_head() -> bool:
    append_log("health check failed; reverting HEAD")
    result = subprocess.run(
        ["git", "revert", "--no-edit", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    append_log(f"git revert exited {result.returncode}\n{result.stdout}\n{result.stderr}")
    return result.returncode == 0


def restart_app(proc: subprocess.Popen | None, reason: str) -> subprocess.Popen:
    append_log(f"restart requested: {reason}")
    stop_app(proc)
    candidate = start_app()
    if health_ok(candidate):
        append_log("health check passed")
        return candidate

    log_error("supervisor", "error", "app failed health check", {"reason": reason})
    stop_app(candidate)
    if ROLLBACK_ON_FAILED_HEALTH and revert_head():
        rolled_back = start_app()
        if health_ok(rolled_back):
            append_log("rollback health check passed")
            return rolled_back
        log_error("supervisor", "critical", "rollback failed health check", {"reason": reason})
        return rolled_back
    return start_app()


def read_restart_reason() -> str:
    try:
        payload = json.loads(RESTART_SIGNAL.read_text())
        reason = str(payload.get("reason") or "file trigger")
    except Exception:
        reason = "file trigger"
    try:
        RESTART_SIGNAL.unlink()
    except FileNotFoundError:
        pass
    return reason


def handle_signal(signum: int, _frame) -> None:
    global stop_requested
    stop_requested = True
    append_log(f"received signal {signum}")


def main() -> int:
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    proc = start_app()
    if health_ok(proc):
        append_log("initial health check passed")
    else:
        log_error("supervisor", "error", "initial app health check failed")

    while not stop_requested:
        if RESTART_SIGNAL.exists():
            proc = restart_app(proc, read_restart_reason())
        elif proc.poll() is not None:
            code = proc.returncode
            log_error("supervisor", "error", f"app exited with code {code}")
            proc = restart_app(proc, f"process exited with code {code}")
        time.sleep(1)

    stop_app(proc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

