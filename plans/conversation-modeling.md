# Plan: Conversation Modeling with Group Chat Support

## Context

Hal currently treats every conversation as a 1:1 chat keyed by phone number. There's no distinction between group and direct messages, no sender tracking on individual messages, and the agent transcript hardcodes "Ian"/"Hal". Blooio already sends `is_group`, `sender`, and `protocol` fields — we just ignore them. This plan adds proper conversation modeling so Hal can participate in group chats (responding only when mentioned) while tracking who said what.

## Implementation Steps

### Step 1: Schema migration (`hal/db.py`)

Add columns via idempotent `ALTER TABLE` statements in `initialize()`:

- **conversations**: add `is_group INTEGER DEFAULT 0`, `protocol TEXT`, `display_name TEXT`
- **messages**: add `sender TEXT`

Update `record_message()` to accept `sender`, `is_group`, `protocol` params. Update the conversation upsert to set `is_group` and `protocol` on insert (latch `is_group` to 1 once set).

Add `get_conversation(conversation_id)` helper to fetch metadata.

Update `get_conversation_messages()` and `get_recent_messages()` SELECTs to include `sender`.

### Step 2: Webhook parsing (`hal/service.py`)

Expand `InboundSMS` dataclass with `sender: str | None`, `is_group: bool`, `protocol: str | None`.

Fix `parse_blooio_payload()`:
- Extract `is_group` from payload
- Extract `sender` as a string (currently ambiguously treated as both dict and string)
- Remove `sender` from `chat_id` candidate list for group chats (use `external_id` as the conversation identifier)
- Extract `protocol`

Add mention detection:
```python
_HAL_MENTION_RE = re.compile(r'\bhal\b', re.IGNORECASE)

def hal_is_mentioned(text: str) -> bool:
    return bool(_HAL_MENTION_RE.search(text))
```

### Step 3: Service routing (`hal/service.py`)

In `handle_inbound_sms()`:
1. Always record the inbound message (with sender, is_group, protocol)
2. If `is_group and not hal_is_mentioned(text)` → return `{"status": "skipped", "reason": "no_mention"}` without generating a reply
3. Otherwise proceed to agent/fallback as before, passing `is_group` and `sender` through

Update `_send_reply` to pass `sender="hal"` when recording outbound messages.

### Step 4: Transcript building (`hal/agent.py`)

Update `build_conversation_transcript()`:
- Fetch conversation metadata via `db.get_conversation()`
- Use `"Group conversation"` header for groups
- Show `row["sender"]` for inbound messages instead of hardcoded "Ian"
- Show "Hal" for outbound, fall back to "Unknown" if sender is null (old data)

### Step 5: Agent prompt (`hal/agent.py`)

Update `run_sms_turn()` and `_build_prompt()` to accept `is_group` and `sender`.

When `is_group=True`, add to the prompt:
- "This is a GROUP conversation"
- "The latest message was sent by: {sender}"
- "You were mentioned, so you should respond"

### Step 6: CLI updates (`hal/cli.py`)

- Add `--sender` and `--is-group` flags to `simulate-inbound`
- Pass `sender="hal"` in `_send_sms` when recording outbound messages

### Step 7: Webhook status (`hal/app.py`)

Mark skipped group messages as `"skipped"` instead of `"processed"` in webhook status.

### Step 8: LLM fallback (`hal/llm.py`)

Update `generate_reply()` to accept `is_group`/`sender`, adjust system prompt and message formatting for group context.

### Step 9: Prompt update (`prompts/system.md`)

Add a line about group conversation awareness.

### Step 10: Tests

- Group webhook without mention → skipped
- Group webhook with mention → response generated
- Sender recorded on messages
- Transcript shows sender phone numbers in group chats
- `hal_is_mentioned()` positive/negative cases
- `parse_blooio_payload()` group vs 1:1 parsing

## Files to modify

| File | What changes |
|------|-------------|
| `hal/db.py` | Migration, record_message sig, query updates, get_conversation |
| `hal/service.py` | InboundSMS, parse_blooio_payload, hal_is_mentioned, routing |
| `hal/agent.py` | Transcript sender names, prompt group context |
| `hal/cli.py` | simulate-inbound flags, send-sms sender |
| `hal/app.py` | Webhook status for skipped messages |
| `hal/llm.py` | Group-aware fallback |
| `prompts/system.md` | Group awareness note |
| `tests/test_app.py` | Group webhook tests |
| `tests/test_agent_harness.py` | Transcript and prompt tests |

## Verification

1. Run `uv run pytest` — all existing tests pass, new tests pass
2. `simulate-inbound` with `--is-group --text "hey Hal what's up"` → gets a reply
3. `simulate-inbound` with `--is-group --text "anyone want lunch?"` → skipped, no reply
4. Check SQLite: `SELECT sender, is_group FROM messages` shows correct values
5. Check transcript output includes sender phone numbers for group messages
