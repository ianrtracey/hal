from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def initialize(self) -> None:
        with self._lock, self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound', 'system')),
                    sender_id TEXT,
                    text TEXT NOT NULL,
                    raw_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
                    ON messages(conversation_id, created_at);

                CREATE TABLE IF NOT EXISTS webhooks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    event_type TEXT,
                    event_id TEXT,
                    chat_id TEXT,
                    text TEXT,
                    status TEXT NOT NULL,
                    headers_json TEXT,
                    payload_json TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_webhooks_created
                    ON webhooks(created_at);

                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS error_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    traceback TEXT,
                    raw_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    inbound_message_id INTEGER REFERENCES messages(id),
                    status TEXT NOT NULL,
                    prompt_files_json TEXT,
                    prompt_text TEXT,
                    command_json TEXT,
                    stdout TEXT,
                    stderr TEXT,
                    returncode INTEGER,
                    outbound_message_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_agent_runs_conversation_created
                    ON agent_runs(conversation_id, created_at);
                """
            )
            # Migration: add sender_id to messages if missing (existing DBs)
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(messages)").fetchall()
            }
            if "sender_id" not in columns:
                conn.execute("ALTER TABLE messages ADD COLUMN sender_id TEXT")

    def health_check(self) -> bool:
        with self._lock, self.connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return True

    def record_message(
        self,
        conversation_id: str,
        direction: str,
        text: str,
        raw: Any | None = None,
        sender_id: str | None = None,
    ) -> int:
        now = utc_now()
        raw_json = json.dumps(raw, sort_keys=True) if raw is not None else None
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations (id, created_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (conversation_id, now, now),
            )
            cursor = conn.execute(
                """
                INSERT INTO messages (conversation_id, direction, sender_id, text, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (conversation_id, direction, sender_id, text, raw_json, now),
            )
            return int(cursor.lastrowid)

    def record_webhook(
        self,
        provider: str,
        status: str,
        payload: Any,
        headers: dict[str, Any] | None = None,
        event_type: str | None = None,
        event_id: str | None = None,
        chat_id: str | None = None,
        text: str | None = None,
    ) -> int:
        now = utc_now()
        with self._lock, self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO webhooks (
                    provider,
                    event_type,
                    event_id,
                    chat_id,
                    text,
                    status,
                    headers_json,
                    payload_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider,
                    event_type,
                    event_id,
                    chat_id,
                    text,
                    status,
                    json.dumps(headers, sort_keys=True) if headers is not None else None,
                    json.dumps(payload, sort_keys=True) if payload is not None else None,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def update_webhook_status(
        self,
        webhook_id: int,
        status: str,
        error_message: str | None = None,
    ) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE webhooks
                SET status = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, error_message, utc_now(), webhook_id),
            )

    def list_webhooks(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._lock, self.connect() as conn:
            return conn.execute(
                """
                SELECT id, provider, event_type, event_id, chat_id, text, status, created_at, updated_at
                FROM webhooks
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def find_webhook_by_event(self, provider: str, event_id: str) -> sqlite3.Row | None:
        with self._lock, self.connect() as conn:
            return conn.execute(
                """
                SELECT id, provider, event_type, event_id, chat_id, text, status, created_at, updated_at
                FROM webhooks
                WHERE provider = ? AND event_id = ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (provider, event_id),
            ).fetchone()

    def get_recent_messages(self, conversation_id: str, limit: int) -> list[sqlite3.Row]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, direction, sender_id, text, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
        return list(reversed(rows))

    def get_conversation_participants(self, conversation_id: str) -> list[str]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT sender_id
                FROM messages
                WHERE conversation_id = ? AND sender_id IS NOT NULL
                """,
                (conversation_id,),
            ).fetchall()
        return [row["sender_id"] for row in rows]

    def get_conversation_messages(
        self,
        conversation_id: str,
        include_system: bool = False,
        limit: int = 500,
    ) -> list[sqlite3.Row]:
        where = "conversation_id = ?"
        params: tuple[Any, ...] = (conversation_id,)
        if not include_system:
            where += " AND direction != ?"
            params = (conversation_id, "system")
        params = (*params, limit)
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, direction, sender_id, text, raw_json, created_at
                FROM messages
                WHERE {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return list(reversed(rows))

    def count_messages_after(
        self,
        conversation_id: str,
        after_message_id: int,
        direction: str | None = None,
    ) -> int:
        where = "conversation_id = ? AND id > ?"
        params: list[Any] = [conversation_id, after_message_id]
        if direction:
            where += " AND direction = ?"
            params.append(direction)
        with self._lock, self.connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS count FROM messages WHERE {where}",
                params,
            ).fetchone()
        return int(row["count"])

    def latest_message_after(
        self,
        conversation_id: str,
        after_message_id: int,
        direction: str | None = None,
    ) -> sqlite3.Row | None:
        where = "conversation_id = ? AND id > ?"
        params: list[Any] = [conversation_id, after_message_id]
        if direction:
            where += " AND direction = ?"
            params.append(direction)
        with self._lock, self.connect() as conn:
            return conn.execute(
                f"""
                SELECT id, direction, sender_id, text, raw_json, created_at
                FROM messages
                WHERE {where}
                ORDER BY id DESC
                LIMIT 1
                """,
                params,
            ).fetchone()

    def record_agent_run_start(
        self,
        conversation_id: str,
        inbound_message_id: int,
        prompt_files: Any,
        prompt_text: str,
        command: Any,
    ) -> int:
        with self._lock, self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO agent_runs (
                    conversation_id,
                    inbound_message_id,
                    status,
                    prompt_files_json,
                    prompt_text,
                    command_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    inbound_message_id,
                    "running",
                    json.dumps(prompt_files, sort_keys=True),
                    prompt_text,
                    json.dumps(command, sort_keys=True),
                    utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def complete_agent_run(
        self,
        run_id: int,
        status: str,
        stdout: str,
        stderr: str,
        returncode: int | None,
        outbound_message_count: int,
    ) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE agent_runs
                SET status = ?,
                    stdout = ?,
                    stderr = ?,
                    returncode = ?,
                    outbound_message_count = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    stdout,
                    stderr,
                    returncode,
                    outbound_message_count,
                    utc_now(),
                    run_id,
                ),
            )

    def list_agent_runs(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._lock, self.connect() as conn:
            return conn.execute(
                """
                SELECT id, conversation_id, inbound_message_id, status, returncode,
                       outbound_message_count, created_at, completed_at
                FROM agent_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def record_task(self, name: str, status: str, payload: Any | None = None) -> int:
        now = utc_now()
        payload_json = json.dumps(payload, sort_keys=True) if payload is not None else None
        with self._lock, self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tasks (name, status, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, status, payload_json, now, now),
            )
            return int(cursor.lastrowid)

    def record_error(
        self,
        source: str,
        severity: str,
        message: str,
        traceback_text: str | None = None,
        raw: Any | None = None,
    ) -> int:
        raw_json = json.dumps(raw, sort_keys=True) if raw is not None else None
        with self._lock, self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO error_logs (source, severity, message, traceback, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source, severity, message, traceback_text, raw_json, utc_now()),
            )
            return int(cursor.lastrowid)

    def list_errors(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._lock, self.connect() as conn:
            return conn.execute(
                """
                SELECT source, severity, message, traceback, raw_json, created_at
                FROM error_logs
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
