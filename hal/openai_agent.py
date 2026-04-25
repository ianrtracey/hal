from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents import Agent, Runner, RunContextWrapper, function_tool, OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from .config import Settings
from .db import Database

logger = logging.getLogger("hal.agent")

PROMPT_FILES = ("system.md", "personality.md")
CONTACTS_DIR = "notes/contacts"


def _contacts_dir(settings: Settings) -> Path:
    return settings.repo_root / CONTACTS_DIR


def _contact_path(settings: Settings, phone_number: str) -> Path:
    safe = re.sub(r"[^\d+]", "", phone_number)
    return _contacts_dir(settings) / f"{safe}.md"


def load_contact_notes(settings: Settings, phone_numbers: list[str]) -> str:
    sections = []
    for number in phone_numbers:
        path = _contact_path(settings, number)
        if path.exists():
            content = path.read_text().strip()
            if content:
                sections.append(f"## {number}\n{content}")
    if not sections:
        return ""
    return "# Contact notes\n\n" + "\n\n".join(sections)


@dataclass
class HalContext:
    chat_id: str
    settings: Settings
    db: Database
    messages_sent: int = 0
    last_reply: str | None = None


@function_tool
async def send_sms(ctx: RunContextWrapper[HalContext], text: str) -> str:
    """Send a visible SMS message to the user."""
    c = ctx.context
    sent = False
    blooio_response = None

    if c.settings.blooio_api_key:
        from blooio_client import BlooioClient

        client = BlooioClient(api_key=c.settings.blooio_api_key)
        try:
            client.stop_typing(c.chat_id)
        except Exception:
            pass
        blooio_response = client.send_message(c.chat_id, text)
        sent = True

    c.db.record_message(
        c.chat_id,
        "outbound",
        text,
        {"blooio_response": blooio_response, "sent": sent, "source": "openai_agent"},
    )
    c.messages_sent += 1
    c.last_reply = text
    return "Message sent."



@function_tool
async def record_note(ctx: RunContextWrapper[HalContext], text: str) -> str:
    """Record an internal note (not visible to the user)."""
    ctx.context.db.record_message(
        ctx.context.chat_id,
        "system",
        text,
        {"source": "openai_agent.note"},
    )
    return "Note recorded."


@function_tool
async def remember_contact(
    ctx: RunContextWrapper[HalContext], phone_number: str, fact: str
) -> str:
    """Remember a fact about a contact. Use this when you learn something worth
    remembering about a person — their name, preferences, relationship to others,
    etc. Each fact is a single line. Existing facts are preserved."""
    c = ctx.context
    path = _contact_path(c.settings, phone_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text().strip() if path.exists() else ""
    line = f"- {fact}"
    if existing:
        # Don't duplicate identical facts
        if line in existing:
            return f"Already known about {phone_number}."
        content = f"{existing}\n{line}\n"
    else:
        content = f"{line}\n"
    path.write_text(content)
    return f"Remembered about {phone_number}: {fact}"


def _load_instructions(settings: Settings) -> str:
    parts = []
    for filename in PROMPT_FILES:
        path = settings.prompt_dir / filename
        try:
            parts.append(path.read_text().strip())
        except FileNotFoundError:
            pass
    return "\n\n".join(parts)


def build_conversation_transcript(db: Database, conversation_id: str) -> str:
    rows = db.get_conversation_messages(conversation_id)
    lines = [f"Conversation with {conversation_id}:"]
    if not rows:
        lines.append("")
        lines.append("(no previous messages)")
        return "\n".join(lines)
    lines.append("")
    for row in rows:
        if row["direction"] == "outbound":
            speaker = "Hal"
        elif row["sender_id"]:
            speaker = row["sender_id"]
        else:
            speaker = "Unknown"
        lines.append(f"[{row['created_at']}] {speaker}: {row['text']}")
    return "\n".join(lines)


@dataclass(frozen=True)
class AgentRunResult:
    ok: bool
    stdout: str
    stderr: str
    outbound_message_count: int
    reply: str | None = None


class OpenAIAgentRunner:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self._client = AsyncOpenAI(
            api_key=settings.anthropic_api_key,
            base_url="https://api.anthropic.com/v1/",
        )
        self._instructions = _load_instructions(settings)

    async def run_sms_turn(
        self,
        chat_id: str,
        latest_text: str,
        inbound_message_id: int,
    ) -> AgentRunResult:
        t0 = time.monotonic()

        transcript = build_conversation_transcript(self.db, chat_id)
        participants = self.db.get_conversation_participants(chat_id)
        contact_notes = load_contact_notes(self.settings, participants)

        parts = []
        if contact_notes:
            parts.append(contact_notes)
        parts.append(transcript)
        parts.append(f"Latest inbound message:\n{latest_text}")
        user_message = "\n\n".join(parts)

        context = HalContext(
            chat_id=chat_id,
            settings=self.settings,
            db=self.db,
        )

        model = OpenAIChatCompletionsModel(
            model=self.settings.anthropic_model,
            openai_client=self._client,
        )

        agent = Agent(
            name="Hal",
            instructions=self._instructions,
            tools=[send_sms, record_note, remember_contact],
            model=model,
        )

        t_prep = time.monotonic()

        try:
            result = await Runner.run(agent, input=user_message, context=context)
            t_done = time.monotonic()

            logger.info(
                "AGENT TIMING prep=%.3fs agent=%.3fs total=%.3fs",
                t_prep - t0, t_done - t_prep, t_done - t0,
            )

            ok = context.messages_sent > 0
            return AgentRunResult(
                ok=ok,
                stdout=str(result.final_output),
                stderr="",
                outbound_message_count=context.messages_sent,
                reply=context.last_reply,
            )
        except Exception as exc:
            t_done = time.monotonic()
            logger.error("Agent error after %.3fs: %s", t_done - t0, exc, exc_info=True)
            return AgentRunResult(
                ok=False,
                stdout="",
                stderr=str(exc),
                outbound_message_count=context.messages_sent,
                reply=context.last_reply,
            )
