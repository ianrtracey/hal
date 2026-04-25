# iMessage Reactions Plan

## Context

Blooio's v2 API supports adding/removing iMessage reactions (tapbacks) on messages. We want Hal to be able to react to inbound messages as a natural communication channel — laughing at jokes, thumbs-up to acknowledge tasks, etc.

**API:** `POST /chats/{chatId}/messages/{messageId}/reactions`
- Body: `{"reaction": "+love"}` (prefix `+` to add, `-` to remove)
- Classic tapbacks: `love`, `like`, `dislike`, `laugh`, `emphasize`, `question`
- Also supports arbitrary emoji reactions (macOS 14+)
- `messageId` can be a Blooio `msg_xxx` ID or a relative index (`-1` for last message, `-2` for second-to-last, etc.)

## Problem: Message ID Tracking

The `messages` table currently has an auto-increment `id` (internal) but does **not** store the Blooio message ID (`msg_xxx`). We need the Blooio message ID to target reactions at specific messages via the API.

However, looking at how the agent works: the agent receives an inbound message and responds in the same turn. The reaction target will almost always be the **message that just came in** — the one that triggered the current turn. We can use relative indexing (`-1`) for the most recent inbound message, which avoids needing to store/look up Blooio IDs entirely.

**Decision: No schema migration needed.** Use Blooio's relative message index (`-1` = last inbound) for reactions. This covers the primary use case (reacting to the message you're currently responding to). If we later need to react to older messages, we can add a `blooio_message_id` column then.

## Changes

### 1. `blooio_client.py` — Add `react_to_message` method

```python
def react_to_message(
    self,
    chat_id: str,
    message_id: str,
    reaction: str,
) -> dict:
    """Add or remove a reaction on a message.

    reaction: e.g. "+love", "+laugh", "-like"
    message_id: Blooio msg_xxx ID or relative index like "-1"
    """
    resp = self.session.post(
        f"{self._chat_url(chat_id)}/messages/{quote(message_id, safe='')}/reactions",
        json={"reaction": reaction},
    )
    resp.raise_for_status()
    return resp.json()
```

### 2. `hal/openai_agent.py` — Add `react` tool

New `@function_tool` that the agent can call:

```python
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
```

Implementation: calls `BlooioClient.react_to_message(chat_id, "-1", f"+{reaction}")` using the relative index `-1` (last inbound message). Records the reaction as a system message in the DB for history.

Register it in the agent's tool list alongside `send_sms`, `record_note`, etc.

### 3. `prompts/system.md` — Add reaction guidance

Add a new section to the system prompt:

```markdown
## Reactions

You can react to the user's latest message with an iMessage tapback using the `react` tool.
Reactions are nonverbal — they don't replace a reply but can complement one or stand alone
when a full reply isn't needed.

Guidelines:
- **like** (thumbs up): Acknowledge tasks, instructions, confirmations. "Got it" energy.
- **love** (heart): When the user shares something kind, personal, or heartfelt.
- **laugh** (haha): When something is genuinely funny. Don't force it.
- **emphasize** (exclamation marks): Surprise, excitement, or "whoa" moments.
- **question** (?): When something is unclear and you need more info.
- **dislike** (thumbs down): Sparingly — commiserate with bad news.

A reaction + short reply often feels more natural than a reply alone.
Don't react to every message — use them when they add something.
```

### 4. `HalContext` — Track reaction state

Add a `reaction_sent: bool = False` field to `HalContext` so we can track whether a reaction was sent during the turn (for logging/debugging in agent run results).

## File Change Summary

| File | Change |
|------|--------|
| `blooio_client.py` | Add `react_to_message()` method |
| `hal/openai_agent.py` | Add `react` function tool, register in agent tool list, update `HalContext` |
| `prompts/system.md` | Add `## Reactions` section with usage guidelines |

## Out of Scope

- **Schema migration for `blooio_message_id`** — not needed with relative indexing
- **Reacting to older messages** — only reacting to the latest inbound for now
- **Removing reactions** — only adding for now
- **Emoji reactions** — sticking to classic tapbacks for simplicity
