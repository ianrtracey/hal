from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from agents import Agent, Runner, RunContextWrapper, function_tool, OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from .config import Settings
from .db import Database

logger = logging.getLogger("hal.agent")

PROMPT_FILES = ("system.md", "personality.md")


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
        user_message = f"{transcript}\n\nLatest inbound message:\n{latest_text}"

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
            tools=[send_sms, record_note],
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
