from __future__ import annotations

import subprocess
from dataclasses import dataclass

from .config import Settings
from .db import Database, utc_now


PROMPT_FILES = ("system.md", "personality.md", "sms_harness.md")


@dataclass(frozen=True)
class AgentRunResult:
    ok: bool
    run_id: int | None
    returncode: int | None
    stdout: str
    stderr: str
    outbound_message_count: int
    reply: str | None = None


def build_conversation_transcript(db: Database, conversation_id: str) -> str:
    rows = db.get_conversation_messages(conversation_id)
    lines = [f"Conversation with {conversation_id}:"]
    if not rows:
        lines.append("")
        lines.append("(no previous messages)")
        return "\n".join(lines)

    lines.append("")
    for row in rows:
        speaker = "Ian" if row["direction"] == "inbound" else "Hal"
        lines.append(f"[{row['created_at']}] {speaker}: {row['text']}")
    return "\n".join(lines)


class ClaudeCodeAgent:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db

    def run_sms_turn(
        self,
        chat_id: str,
        latest_text: str,
        inbound_message_id: int,
    ) -> AgentRunResult:
        prompt_files = self._load_prompt_files()
        transcript = build_conversation_transcript(self.db, chat_id)
        prompt = self._build_prompt(chat_id, latest_text, transcript, prompt_files)
        command = self._command()
        run_id = self.db.record_agent_run_start(
            chat_id,
            inbound_message_id,
            prompt_files,
            prompt,
            command,
        )

        stdout = ""
        stderr = ""
        returncode: int | None = None
        try:
            result = subprocess.run(
                command,
                input=prompt,
                cwd=self.settings.repo_root,
                capture_output=True,
                text=True,
                timeout=self.settings.claude_timeout_seconds,
                check=False,
            )
            stdout = result.stdout
            stderr = result.stderr
            returncode = result.returncode
        except FileNotFoundError as exc:
            stderr = f"Claude command not found: {self.settings.claude_command[0]} ({exc})"
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = (exc.stderr or "") + f"\nTimed out after {self.settings.claude_timeout_seconds}s"

        outbound_count = self.db.count_messages_after(chat_id, inbound_message_id, "outbound")
        latest_reply = self.db.latest_message_after(chat_id, inbound_message_id, "outbound")
        ok = outbound_count > 0
        self.db.complete_agent_run(
            run_id,
            "completed" if ok else "failed",
            stdout,
            stderr,
            returncode,
            outbound_count,
        )
        return AgentRunResult(
            ok=ok,
            run_id=run_id,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            outbound_message_count=outbound_count,
            reply=latest_reply["text"] if latest_reply else None,
        )

    def _load_prompt_files(self) -> dict[str, str]:
        prompt_files: dict[str, str] = {}
        for filename in PROMPT_FILES:
            path = self.settings.prompt_dir / filename
            label = str(path)
            try:
                label = str(path.relative_to(self.settings.repo_root))
            except ValueError:
                pass
            try:
                prompt_files[label] = path.read_text()
            except FileNotFoundError:
                prompt_files[label] = ""
        return prompt_files

    def _build_prompt(
        self,
        chat_id: str,
        latest_text: str,
        transcript: str,
        prompt_files: dict[str, str],
    ) -> str:
        prompt_sections = "\n\n".join(
            f"--- {path} ---\n{content.strip()}" for path, content in prompt_files.items()
        )
        return f"""{prompt_sections}

--- Runtime turn context ---
Current timestamp: {utc_now()}
Repository cwd: {self.settings.repo_root}
Chat ID: {chat_id}

Allowed user-visible command:
uv run python -m hal.cli send-sms --chat-id "{chat_id}" --text "..."

Allowed thinking-state commands:
uv run python -m hal.cli thinking --chat-id "{chat_id}" --state on
uv run python -m hal.cli thinking --chat-id "{chat_id}" --state off

Allowed internal note command:
uv run python -m hal.cli note --chat-id "{chat_id}" --text "..."

Send exactly one SMS response for this turn by calling the send-sms command.
Keep the response concise and appropriate for SMS.
Do not claim you took an action unless you actually took it.

Full conversation transcript from SQLite:
{transcript}

Latest inbound message:
{latest_text}
"""

    def _command(self) -> list[str]:
        return [
            *self.settings.claude_command,
            "-p",
            "--output-format",
            "text",
            "--no-session-persistence",
            "--allowedTools",
            "Bash",
            "--permission-mode",
            "acceptEdits",
        ]
