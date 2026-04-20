from __future__ import annotations

import json
import logging
import traceback
from dataclasses import dataclass
from typing import Any

from blooio_client import BlooioClient

from .agent import ClaudeCodeAgent
from .config import Settings
from .db import Database
from .llm import LLMClient


logger = logging.getLogger("hal.service")


@dataclass(frozen=True)
class InboundSMS:
    chat_id: str
    text: str
    raw: dict[str, Any]
    message_id: str | None = None


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def parse_blooio_payload(payload: dict[str, Any]) -> InboundSMS:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    chat = payload.get("chat") if isinstance(payload.get("chat"), dict) else {}
    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}

    chat_id = _first_string(
        payload.get("chat_id"),
        payload.get("conversation_id"),
        payload.get("sender"),
        payload.get("external_id"),
        payload.get("from"),
        payload.get("phone_number"),
        chat.get("id"),
        chat.get("phone_number"),
        message.get("chat_id"),
        message.get("from"),
        sender.get("phone_number"),
        sender.get("id"),
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

    return InboundSMS(chat_id=chat_id, text=text, raw=payload, message_id=message_id)


class HalService:
    def __init__(self, settings: Settings, db: Database, llm: LLMClient):
        self.settings = settings
        self.db = db
        self.llm = llm
        self.agent = ClaudeCodeAgent(settings, db) if settings.agent_enabled else None

    def handle_inbound_sms(self, payload: dict[str, Any]) -> dict[str, Any]:
        inbound = parse_blooio_payload(payload)
        logger.info("Inbound SMS from %s: %s", inbound.chat_id, inbound.text)
        inbound_message_id = self.db.record_message(
            inbound.chat_id,
            "inbound",
            inbound.text,
            inbound.raw,
        )

        try:
            if self.agent:
                result = self.agent.run_sms_turn(
                    inbound.chat_id,
                    inbound.text,
                    inbound_message_id,
                )
                if result.ok:
                    logger.info("Agent outbound SMS to %s: %s", inbound.chat_id, result.reply)
                    return {
                        "chat_id": inbound.chat_id,
                        "reply": result.reply,
                        "sent": self._latest_outbound_sent(inbound.chat_id, inbound_message_id),
                        "agent_run_id": result.run_id,
                    }

                self.db.record_error(
                    source="agent.claude_code",
                    severity="error",
                    message=result.stderr or "Claude Code did not record an outbound SMS",
                    raw={
                        "chat_id": inbound.chat_id,
                        "run_id": result.run_id,
                        "returncode": result.returncode,
                        "stdout": result.stdout,
                    },
                )
                fallback = "I hit an issue generating a reply."
                sent = self._send_reply(inbound.chat_id, fallback, use_typing=False)
                return {
                    "chat_id": inbound.chat_id,
                    "reply": fallback,
                    "sent": sent,
                    "agent_run_id": result.run_id,
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

    def _latest_outbound_sent(self, chat_id: str, inbound_message_id: int) -> bool:
        row = self.db.latest_message_after(chat_id, inbound_message_id, "outbound")
        if not row or not row["raw_json"]:
            return False
        try:
            raw = json.loads(row["raw_json"])
        except Exception:
            return False
        return bool(raw.get("sent"))
