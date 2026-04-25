from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .config import Settings


SYSTEM_PROMPT = """You are Hal, Ian's local personal assistant.
Reply concisely and use the available conversation context.
Do not claim to have taken local actions unless the calling code actually performed them."""


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def generate_reply(
        self,
        conversation_id: str,
        user_text: str,
        history: Sequence[Any],
    ) -> str:
        if not self.settings.anthropic_api_key:
            return f"I received: {user_text}"

        from anthropic import Anthropic

        client = Anthropic(api_key=self.settings.anthropic_api_key)
        messages = self._format_messages(history)
        if not messages or messages[-1]["content"] != user_text:
            messages.append({"role": "user", "content": user_text})

        response = client.messages.create(
            model=self.settings.anthropic_model,
            max_tokens=800,
            system=SYSTEM_PROMPT,
            messages=messages,
            metadata={"user_id": conversation_id},
        )
        text_parts = [
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ]
        reply = "".join(text_parts).strip()
        return reply or "I hit an empty model response."

    def _format_messages(self, history: Sequence[Any]) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for row in history:
            direction = row["direction"]
            if direction == "system":
                continue
            role = "assistant" if direction == "outbound" else "user"
            content = row["text"]
            if content:
                sender_id = row.get("sender_id") if hasattr(row, "keys") else None
                if role == "user" and sender_id:
                    content = f"[{sender_id}] {content}"
                messages.append({"role": role, "content": content})
        return messages

