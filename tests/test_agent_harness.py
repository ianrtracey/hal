from __future__ import annotations

import json
import sys

from hal.agent import ClaudeCodeAgent, build_conversation_transcript
from hal.cli import main as cli_main
from hal.config import get_settings
from hal.db import Database


def test_send_sms_cli_records_outbound_without_blooio_key(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "hal.sqlite3"
    monkeypatch.setenv("HAL_DB_PATH", str(db_path))
    monkeypatch.setenv("HAL_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BLOOIO_API_KEY", raising=False)

    exit_code = cli_main(["send-sms", "--chat-id", "+15551234567", "--text", "hello"])

    assert exit_code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
    assert body["sent"] is False

    rows = Database(db_path).get_recent_messages("+15551234567", limit=10)
    assert [(row["direction"], row["text"]) for row in rows] == [("outbound", "hello")]


def test_transcript_uses_sqlite_messages_and_skips_system_events(tmp_path):
    db = Database(tmp_path / "hal.sqlite3")
    db.record_message("+15551234567", "inbound", "hello")
    db.record_message("+15551234567", "system", "thinking:on")
    db.record_message("+15551234567", "outbound", "hey")

    transcript = build_conversation_transcript(db, "+15551234567")

    assert "Ian: hello" in transcript
    assert "Hal: hey" in transcript
    assert "thinking:on" not in transcript


def test_claude_code_agent_invokes_command_and_records_run(tmp_path, monkeypatch):
    db_path = tmp_path / "hal.sqlite3"
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "system.md").write_text("SYSTEM_UNIQUE")
    (prompt_dir / "personality.md").write_text("PERSONALITY_UNIQUE")
    (prompt_dir / "sms_harness.md").write_text("HARNESS_UNIQUE")
    fake_claude = tmp_path / "fake_claude.py"
    fake_claude.write_text(
        """
from __future__ import annotations

import subprocess
import sys

prompt = sys.stdin.read()
assert "SYSTEM_UNIQUE" in prompt
assert "PERSONALITY_UNIQUE" in prompt
assert "HARNESS_UNIQUE" in prompt
assert "Ian: hello" in prompt
subprocess.run(
    [
        sys.executable,
        "-m",
        "hal.cli",
        "send-sms",
        "--chat-id",
        "+15551234567",
        "--text",
        "agent reply",
    ],
    check=True,
)
print("fake claude done")
"""
    )

    monkeypatch.setenv("HAL_DB_PATH", str(db_path))
    monkeypatch.setenv("HAL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HAL_AGENT_ENABLED", "1")
    monkeypatch.setenv("HAL_PROMPT_DIR", str(prompt_dir))
    monkeypatch.setenv("HAL_CLAUDE_COMMAND", f"{sys.executable} {fake_claude}")
    monkeypatch.delenv("BLOOIO_API_KEY", raising=False)

    db = Database(db_path)
    inbound_id = db.record_message("+15551234567", "inbound", "hello")
    result = ClaudeCodeAgent(get_settings(), db).run_sms_turn(
        "+15551234567",
        "hello",
        inbound_id,
    )

    assert result.ok is True
    assert result.reply == "agent reply"
    assert result.outbound_message_count == 1

    rows = db.get_recent_messages("+15551234567", limit=10)
    assert [(row["direction"], row["text"]) for row in rows] == [
        ("inbound", "hello"),
        ("outbound", "agent reply"),
    ]
    runs = db.list_agent_runs(limit=10)
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["outbound_message_count"] == 1
