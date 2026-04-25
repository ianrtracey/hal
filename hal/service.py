from __future__ import annotations

import json
import logging
import re
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

from blooio_client import BlooioClient

from .config import Settings
from .db import Database
from .llm import LLMClient
from .openai_agent import OpenAIAgentRunner


logger = logging.getLogger("hal.service")


@dataclass(frozen=True)
class InboundSMS:
    chat_id: str
    text: str
    raw: dict[str, Any]
    message_id: str | None = None
    sender_id: str | None = None
    is_group: bool = False
    group_id: str | None = None
    participants: list[str] = field(default_factory=list)


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def parse_blooio_payload(payload: dict[str, Any]) -> InboundSMS:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    chat = payload.get("chat") if isinstance(payload.get("chat"), dict) else {}
    sender_obj = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}

    is_group = bool(payload.get("is_group", False))
    group_id = _first_string(payload.get("group_id"), chat.get("group_id"))

    # Extract participants list from group webhook payloads
    raw_participants = payload.get("participants") or []
    participants = [
        p.get("identifier")
        for p in raw_participants
        if isinstance(p, dict) and p.get("identifier")
    ]

    # Extract sender_id: who actually sent this message
    sender_id = _first_string(
        payload.get("sender") if isinstance(payload.get("sender"), str) else None,
        sender_obj.get("phone_number"),
        sender_obj.get("id"),
        message.get("from"),
    )

    # Extract chat_id: the conversation to reply to.
    # For group chats, prefer group_id over other identifiers.
    chat_id = _first_string(
        group_id,
        payload.get("chat_id"),
        payload.get("conversation_id"),
        payload.get("external_id"),
        payload.get("from"),
        payload.get("phone_number"),
        chat.get("id"),
        chat.get("phone_number"),
        message.get("chat_id"),
        # Fall back to sender only for 1:1 chats
        sender_id,
    )

    text = _first_string(
        payload.get("text"),
        payload.get("body"),
        payload.get("content"),
        message.get("text"),
        message.get("body"),
        message.get("content"),
    )
    message_id = _first_string(payload.get("id"), message.get("id"), payload.get("message_id"))

    if not chat_id:
        raise ValueError("Blooio webhook payload did not include a chat id")
    if not text:
        raise ValueError("Blooio webhook payload did not include message text")

    return InboundSMS(
        chat_id=chat_id,
        text=text,
        raw=payload,
        message_id=message_id,
        sender_id=sender_id,
        is_group=is_group,
        group_id=group_id,
        participants=participants,
    )


class HalService:
    def __init__(self, settings: Settings, db: Database, llm: LLMClient):
        self.settings = settings
        self.db = db
        self.llm = llm
        self.agent = OpenAIAgentRunner(settings, db) if settings.agent_enabled else None

    async def handle_inbound_sms(self, payload: dict[str, Any]) -> dict[str, Any]:
        t0 = time.monotonic()
        inbound = parse_blooio_payload(payload)
        t_parse = time.monotonic()
        logger.info("Inbound SMS from %s: %s", inbound.chat_id, inbound.text)
        inbound_message_id = self.db.record_message(
            inbound.chat_id,
            "inbound",
            inbound.text,
            inbound.raw,
            sender_id=inbound.sender_id,
        )
        t_db = time.monotonic()

        if inbound.is_group and not self._mentions_hal(inbound.text):
            logger.info("Group message without Hal mention, skipping reply")
            return {"chat_id": inbound.chat_id, "reply": None, "sent": False, "skipped": "no_mention"}

        try:
            if self.agent:
                self._start_typing(inbound.chat_id)
                result = await self.agent.run_sms_turn(
                    inbound.chat_id,
                    inbound.text,
                    inbound_message_id,
                    webhook_participants=inbound.participants or None,
                )
                t_agent = time.monotonic()
                logger.info(
                    "TIMING parse=%.3fs db_record=%.3fs agent=%.3fs total=%.3fs",
                    t_parse - t0, t_db - t_parse, t_agent - t_db, t_agent - t0,
                )
                if result.ok:
                    logger.info("Agent outbound SMS to %s: %s", inbound.chat_id, result.reply)
                    return {
                        "chat_id": inbound.chat_id,
                        "reply": result.reply,
                        "sent": self._latest_outbound_sent(inbound.chat_id, inbound_message_id),
                    }

                self.db.record_error(
                    source="agent.openai_sdk",
                    severity="error",
                    message=result.stderr or "Agent did not send an outbound SMS",
                    raw={
                        "chat_id": inbound.chat_id,
                        "stdout": result.stdout,
                    },
                )
                fallback = "I hit an issue generating a reply."
                sent = self._send_reply(inbound.chat_id, fallback, use_typing=False)
                return {
                    "chat_id": inbound.chat_id,
                    "reply": fallback,
                    "sent": sent,
                }

            history = self.db.get_recent_messages(
                inbound.chat_id,
                limit=self.settings.max_context_messages,
            )
            reply = self.llm.generate_reply(inbound.chat_id, inbound.text, history)
            sent = self._send_reply(inbound.chat_id, reply, use_typing=True)
            return {"chat_id": inbound.chat_id, "reply": reply, "sent": sent}
        except Exception as exc:
            self.db.record_error(
                source="webhook.blooio",
                severity="error",
                message=str(exc),
                traceback_text=traceback.format_exc(),
                raw=payload,
            )
            raise

    def _send_reply(self, chat_id: str, reply: str, use_typing: bool) -> bool:
        sent_response = None
        sent = False
        if self.settings.blooio_api_key:
            client = BlooioClient(api_key=self.settings.blooio_api_key)
            if use_typing:
                with client.typing(chat_id):
                    sent_response = client.send_message(chat_id, reply)
            else:
                sent_response = client.send_message(chat_id, reply)
            sent = True

        self.db.record_message(
            chat_id,
            "outbound",
            reply,
            {"blooio_response": sent_response, "sent": sent},
        )
        logger.info("Outbound SMS to %s: %s", chat_id, reply)
        return sent

    _HAL_MENTION_RE = re.compile(r"\bhal\b|@hal", re.IGNORECASE)

    def _mentions_hal(self, text: str) -> bool:
        return bool(self._HAL_MENTION_RE.search(text))

    def _start_typing(self, chat_id: str) -> None:
        if self.settings.blooio_api_key:
            try:
                BlooioClient(api_key=self.settings.blooio_api_key).start_typing(chat_id)
            except Exception:
                pass

    def _stop_typing(self, chat_id: str) -> None:
        if self.settings.blooio_api_key:
            try:
                BlooioClient(api_key=self.settings.blooio_api_key).stop_typing(chat_id)
            except Exception:
                pass

    def _latest_outbound_sent(self, chat_id: str, inbound_message_id: int) -> bool:
        row = self.db.latest_message_after(chat_id, inbound_message_id, "outbound")
        if not row or not row["raw_json"]:
            return False
        try:
            raw = json.loads(row["raw_json"])
        except Exception:
            return False
        return bool(raw.get("sent"))
