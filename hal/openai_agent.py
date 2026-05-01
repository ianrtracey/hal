from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agents import Agent, Runner, RunContextWrapper, function_tool, OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from .config import Settings
from .db import Database
from .tools.fetch_page import fetch_page

logger = logging.getLogger("hal.agent")

PROMPT_FILES = ("system.md", "personality.md")
CONTACTS_DIR = "notes/contacts"
CHATS_DIR = "notes/chats"


def _safe_filename(value: str) -> str:
    return re.sub(r"[^\d+\w-]", "", value)


def _contact_path(settings: Settings, phone_number: str) -> Path:
    return settings.repo_root / CONTACTS_DIR / f"{_safe_filename(phone_number)}.md"


def _chat_path(settings: Settings, chat_id: str) -> Path:
    return settings.repo_root / CHATS_DIR / f"{_safe_filename(chat_id)}.md"


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


def load_chat_notes(settings: Settings, chat_id: str) -> str:
    path = _chat_path(settings, chat_id)
    if path.exists():
        content = path.read_text().strip()
        if content:
            return f"# Chat notes for {chat_id}\n\n{content}"
    return ""


@dataclass
class HalContext:
    chat_id: str
    settings: Settings
    db: Database
    messages_sent: int = 0
    last_reply: str | None = None
    reaction_sent: bool = False
    group_created: bool = False


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



VALID_REACTIONS = {"love", "like", "dislike", "laugh", "emphasize", "question"}


@function_tool
async def react(ctx: RunContextWrapper[HalContext], reaction: str) -> str:
    """React to the user's latest message with an iMessage tapback.

    Use this to add nonverbal reactions that feel natural in iMessage:
    - "love" — when the user shares something heartfelt, kind, or you genuinely appreciate it
    - "like" — to acknowledge receipt of a task, instruction, or plan (thumbs up)
    - "laugh" — when something is genuinely funny
    - "emphasize" — for surprise, excitement, or to highlight something important
    - "question" — when something is unclear or you need clarification
    - "dislike" — rarely; for something unfortunate or bad news the user shares

    You can also react AND reply in the same turn — a reaction + short reply
    often feels more natural than a reply alone.
    """
    c = ctx.context
    cleaned = reaction.strip().lower().lstrip("+-")
    if cleaned not in VALID_REACTIONS:
        return f"Invalid reaction '{reaction}'. Use one of: {', '.join(sorted(VALID_REACTIONS))}"

    if c.settings.blooio_api_key:
        from blooio_client import BlooioClient

        client = BlooioClient(api_key=c.settings.blooio_api_key)
        client.react_to_message(c.chat_id, "-1", f"+{cleaned}")

    c.db.record_message(
        c.chat_id,
        "system",
        f"[reacted with {cleaned}]",
        {"source": "openai_agent.react", "reaction": cleaned},
    )
    c.reaction_sent = True
    return f"Reacted with {cleaned}."


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


@function_tool
async def create_group_chat(
    ctx: RunContextWrapper[HalContext],
    name: str,
    members: list[str],
    initial_message: str,
) -> str:
    """Create a new iMessage group chat and send the first message.

    Use this when the user asks you to start a group chat or group conversation.
    The first message is required to establish the iMessage link.

    Args:
        name: A name for the group (e.g. "Wedding Planning")
        members: List of phone numbers to include (e.g. ["+15551234567", "+15559876543"]).
                 Always include the current user's number.
        initial_message: The first message to send in the group.
    """
    c = ctx.context
    if not c.settings.blooio_api_key:
        return "Error: Blooio API key not configured."

    from blooio_client import BlooioClient

    client = BlooioClient(api_key=c.settings.blooio_api_key)
    group = client.create_group(name, members=members)
    group_id = group.get("group_id")
    if not group_id:
        return f"Error creating group: {group}"

    msg = client.send_message(group_id, initial_message)

    c.db.record_message(
        group_id,
        "outbound",
        initial_message,
        {"blooio_response": msg, "sent": True, "source": "openai_agent.create_group_chat"},
    )

    c.group_created = True
    return f"Group '{name}' created (ID: {group_id}) with {len(members)} members. Initial message sent. Now use send_sms to confirm to the user."


@function_tool
async def remember_chat(ctx: RunContextWrapper[HalContext], fact: str) -> str:
    """Remember a fact about the current chat or group. Use this for things specific
    to the conversation — group plans, shared decisions, running topics, group name,
    etc. Not for facts about individual people (use remember_contact for that)."""
    c = ctx.context
    path = _chat_path(c.settings, c.chat_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text().strip() if path.exists() else ""
    line = f"- {fact}"
    if existing:
        if line in existing:
            return "Already known about this chat."
        content = f"{existing}\n{line}\n"
    else:
        content = f"{line}\n"
    path.write_text(content)
    return f"Remembered about chat: {fact}"


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
        webhook_participants: list[str] | None = None,
    ) -> AgentRunResult:
        t0 = time.monotonic()

        transcript = build_conversation_transcript(self.db, chat_id)
        db_participants = self.db.get_conversation_participants(chat_id)
        # Merge DB participants with webhook participants for full coverage
        all_numbers = set(db_participants)
        if webhook_participants:
            all_numbers.update(webhook_participants)
        participants = sorted(all_numbers)
        contact_notes = load_contact_notes(self.settings, participants)
        chat_notes = load_chat_notes(self.settings, chat_id)

        parts = []
        if chat_notes:
            parts.append(chat_notes)
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

        now = datetime.now(ZoneInfo("America/New_York"))
        time_header = f"Current date and time: {now.strftime('%A, %B %-d, %Y %I:%M %p %Z')}"
        instructions = f"{time_header}\n\n{self._instructions}"

        agent = Agent(
            name="Hal",
            instructions=instructions,
            tools=[send_sms, react, record_note, remember_contact, remember_chat, create_group_chat, fetch_page],
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

            ok = context.messages_sent > 0 or context.group_created or context.reaction_sent
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
