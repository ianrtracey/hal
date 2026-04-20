# V0 Agent Harness Technical Spec

## Summary

Build the smallest useful loop:

```text
SMS webhook -> SQLite chat history -> Claude Code agent -> halctl send-sms
```

For now, ignore self-improvement, restarts, deploys, rollbacks, outbox workers, and advanced concurrency. The goal is to prove that an SMS can reach Hal, Hal can run Claude Code with the full local chat context, and Claude Code can emit a response through a local CLI.

The product goal for this phase is prompt and personality iteration: Ian should be able to converse with Hal over SMS, tweak local prompt/personality files, send another SMS, and inspect how the Claude Code harness behaved.

SQLite should be the source of chat context. Blooio should be used for receiving and sending messages, not as the conversation memory store.

## Goals

- Receive inbound SMS through the existing Blooio webhook.
- Store every inbound and outbound message in SQLite.
- Build a full conversation transcript for the current chat from SQLite.
- Run Claude Code/Agent SDK with that transcript.
- Give Claude a local `halctl` command for sending SMS.
- Get one reliable response back to the user.
- Keep prompts/personality on disk so they can be edited without touching Python code.
- Record each Claude Code run enough to debug prompt changes and harness behavior.

## Non-Goals

- No self-modifying code path.
- No restart protocol.
- No deploy or rollback flow.
- No background task queue unless the webhook timeout forces it.
- No multi-agent orchestration.
- No durable outbound outbox yet.
- No Blooio API lookup for historical chat context.
- No polished admin UI for prompt editing.

## V0 Architecture

```text
Blooio
  -> POST /webhooks/blooio
  -> parse inbound SMS
  -> save inbound message to SQLite
  -> load full chat transcript from SQLite
  -> run Claude Code agent
  -> agent calls halctl send-sms
  -> halctl sends through Blooio and records outbound message
  -> webhook returns success
```

This can stay synchronous for v0 if Claude usually responds quickly enough. If Blooio requires a fast webhook response, split only the last part into a background task:

```text
webhook -> save inbound -> enqueue in SQLite -> return 200
worker -> run agent -> send response
```

Do not build the larger queue/outbox system until this simple loop works.

## SQLite Chat Context

Use the existing `messages` table as the source of truth.

Existing fields are enough for v0:

- `conversation_id`
- `direction`: `inbound`, `outbound`, `system`
- `text`
- `raw_json`
- `created_at`

For each inbound message:

1. Parse `chat_id` and `text`.
2. Insert the inbound row.
3. Query all messages for that `conversation_id`, ordered by creation/id.
4. Render those rows into a transcript.
5. Pass that transcript to the agent.

For v0, "full context window" means all SQLite messages for that chat. Later, if the transcript gets too large, add summarization or sliding-window truncation.

Recommended transcript format:

```text
Conversation with +15551234567:

[2026-04-19T00:00:00Z] Ian: hello
[2026-04-19T00:00:04Z] Hal: hey
[2026-04-19T00:01:12Z] Ian: can you check my calendar?
```

Do not fetch prior messages from Blooio. Blooio is not the memory layer.

## Claude Code Invocation

Use Claude Code through the Claude Agent SDK or `claude -p` in non-interactive mode.

Preferred path:

```text
Python service -> Claude Agent SDK query(...) -> stream/collect result
```

Simpler first spike:

```text
Python service -> subprocess: claude -p "<prompt>" --cwd <repo>
```

The first spike is acceptable if it proves the loop faster. Keep the interface small so it can be swapped for the SDK later.

## Prompt Files

Prompts should be plain text files in the repo, loaded at runtime:

```text
prompts/
  system.md
  personality.md
  sms_harness.md
```

Suggested responsibilities:

- `system.md`: stable identity and operating rules for Hal.
- `personality.md`: tone, style, preferences, and SMS voice.
- `sms_harness.md`: mechanical instructions for this harness, including how to call `halctl send-sms`.

Changing these files should affect the next inbound SMS without a code change. A process restart is acceptable for v0 if the app caches settings at startup, but hot-reload is better if trivial.

All three prompt files are injected on every agent invocation in v0.

This is deliberate. V0 should use a fresh Claude Code run for each SMS turn, with the conversation state rebuilt from SQLite. That makes prompt/personality edits immediately observable on the next SMS and avoids hidden behavior from a long-lived Claude session.

Later, if we switch to SDK session resume, the harness should still re-inject the current prompt file contents each turn or explicitly log that a run used stale session instructions.

## Agent Prompt

The runtime prompt should be assembled from the prompt files plus the current turn data. It should include:

- The current inbound SMS text.
- The full SQLite transcript for this chat.
- A clear instruction that this is an SMS assistant.
- A clear instruction to send exactly one SMS response using `halctl send-sms`.
- The exact `chat_id`.

Example:

```text
<contents of prompts/system.md>

<contents of prompts/personality.md>

<contents of prompts/sms_harness.md>

You are Hal, Ian's personal SMS assistant.

You are responding to chat_id: +15551234567.

Use this command to send the user-visible reply:

uv run python -m hal.cli send-sms --chat-id "+15551234567" --text "..."

Send exactly one SMS response for this turn. Keep it concise.
Do not claim you did something unless you actually did it.

Full conversation transcript:
<transcript>

Latest inbound message:
<message>
```

For v0, the agent should not need arbitrary custom tools. It can use Bash to call `halctl`.

## Per-Turn Agent Context

Each SMS turn should pass exactly this context to Claude Code:

- Current timestamp.
- `chat_id`.
- Latest inbound message ID and text.
- Full SQLite transcript for that `chat_id`, including the latest inbound message.
- Contents of `prompts/system.md`.
- Contents of `prompts/personality.md`.
- Contents of `prompts/sms_harness.md`.
- The allowed `halctl` commands and examples.
- The current working directory for local repo access.

Do not pass:

- Blooio chat history fetched from the API.
- Other users' or chats' transcripts.
- Raw secrets or `.env` contents.
- Prior Claude Code session transcripts, unless intentionally debugging the harness.

Claude Code can inspect local files from the repo when needed, but the SMS conversation memory should come from SQLite, not from Claude Code session state.

## Harness Observability

Because the purpose of this phase is to tune prompts and observe Claude Code behavior, every run should be inspectable.

Record:

- conversation ID
- inbound message ID
- prompt files used
- final assembled prompt
- Claude command or SDK options
- stdout
- stderr
- return code
- start and completion timestamps
- whether an outbound message was recorded

This can be stored in SQLite and surfaced later through a simple CLI command. For v0, direct SQLite inspection is acceptable.

## `halctl` V0 CLI

Add a local CLI module:

```text
hal/cli.py
```

Required commands:

```bash
uv run python -m hal.cli send-sms --chat-id "+15551234567" --text "hello"
uv run python -m hal.cli thinking --chat-id "+15551234567" --state on
uv run python -m hal.cli thinking --chat-id "+15551234567" --state off
uv run python -m hal.cli note --chat-id "+15551234567" --text "Running Claude Code"
uv run python -m hal.cli simulate-inbound --chat-id "+15551234567" --text "hello"
```

### `send-sms`

Sends a user-visible SMS.

```bash
uv run python -m hal.cli send-sms \
  --chat-id "+15551234567" \
  --text "hello"
```

Behavior:

1. Load settings.
2. Send SMS with `BlooioClient.send_message(chat_id, text)`.
3. Record the outbound message in SQLite.
4. Print JSON to stdout.

Example stdout:

```json
{
  "ok": true,
  "chat_id": "+15551234567",
  "message_id": 42,
  "sent": true
}
```

If `BLOOIO_API_KEY` is missing, record the outbound message with `sent: false` and return JSON. That keeps local testing easy.

### `thinking`

Controls the user's visible "Hal is working" state. In Blooio this maps to typing indicators.

```bash
uv run python -m hal.cli thinking --chat-id "+15551234567" --state on
uv run python -m hal.cli thinking --chat-id "+15551234567" --state off
```

Behavior:

1. Load settings.
2. If `state=on`, call `BlooioClient.start_typing(chat_id)`.
3. If `state=off`, call `BlooioClient.stop_typing(chat_id)`.
4. Record a system message or agent event in SQLite.
5. Print JSON to stdout.

Example stdout:

```json
{
  "ok": true,
  "chat_id": "+15551234567",
  "state": "on",
  "sent": true
}
```

If `BLOOIO_API_KEY` is missing, record the event with `sent: false` and return JSON.

### `note`

Records an internal harness note for debugging. It does not send an SMS.

```bash
uv run python -m hal.cli note \
  --chat-id "+15551234567" \
  --text "Running Claude Code with updated personality prompt"
```

Behavior:

1. Record a `system` message or `agent_runs` event.
2. Print JSON to stdout.

This is useful when inspecting how the agent moved through a turn.

### `simulate-inbound`

Runs the same post-parse code path as an inbound webhook without requiring Blooio.

```bash
uv run python -m hal.cli simulate-inbound \
  --chat-id "+15551234567" \
  --text "hello"
```

Behavior:

1. Record the inbound message in SQLite.
2. Build the transcript.
3. Run Claude Code.
4. Let Claude Code call `send-sms`.
5. Print the final run JSON.

This command is specifically for fast prompt/personality iteration.

## Basic Flow

### Happy Path

```text
1. Blooio sends webhook.
2. FastAPI verifies and parses payload.
3. Hal records inbound message.
4. Hal builds transcript from SQLite.
5. Hal invokes Claude Code with prompt files, transcript, latest message, and halctl command contract.
6. Claude optionally calls halctl thinking on/off.
7. Claude calls halctl send-sms.
8. halctl sends via Blooio and records outbound message.
9. FastAPI returns success.
```

### Agent Does Not Send

If the Claude process exits without an outbound message being recorded for the current turn, Hal should send a fallback:

```text
I hit an issue generating a reply.
```

This can be implemented by checking whether a new outbound row exists after the agent process exits.

### Agent Sends More Than Once

For v0, this is a prompt violation but not a disaster. Record both messages. Later, add a `turn_id` and enforce one send per turn.

## Minimal Data Additions

The existing message schema can support chat memory. Add one debug table for harness iteration:

### `agent_runs`

Tracks each Claude Code invocation.

Fields:

- `id`
- `conversation_id`
- `inbound_message_id`
- `status`: running, completed, failed
- `prompt_files_json`
- `prompt_text`
- `command_json`
- `stdout`
- `stderr`
- `returncode`
- `outbound_message_count`
- `created_at`
- `completed_at`

This table is part of the v0 goal because it lets us compare prompt/personality changes against observed harness behavior.

## Implementation Plan

### Step 1: Add `hal.cli`

Implement:

```bash
uv run python -m hal.cli send-sms --chat-id ... --text ...
```

Reuse:

- `Settings`
- `Database`
- `BlooioClient`

Add tests for:

- outbound row is recorded
- missing `BLOOIO_API_KEY` does not crash
- command prints JSON

### Step 2: Add Prompt Files

Create:

```text
prompts/system.md
prompts/personality.md
prompts/sms_harness.md
```

Keep them short initially. The point is fast iteration.

### Step 3: Add Transcript Builder

Add a small function:

```python
build_conversation_transcript(db, conversation_id) -> str
```

It should read every message for the chat from SQLite in chronological order and render it as plain text.

### Step 4: Add Claude Code Runner

Add a runner with one public method:

```python
run_sms_turn(chat_id: str, latest_text: str) -> AgentRunResult
```

It should:

- load prompt files
- build the transcript
- construct the prompt
- run Claude Code
- capture stdout/stderr/return code
- record an `agent_runs` row
- verify an outbound message was recorded

Start with subprocess invocation if that is fastest. Move to Agent SDK when this works.

### Step 5: Wire Webhook to Runner

Replace the current direct `LLMClient.generate_reply()` call with:

```text
record inbound -> run_sms_turn -> return result
```

Keep the existing fallback behavior when Claude Code is unavailable.

### Step 6: Add Local Simulation Command

Add a command for prompt iteration without sending a real SMS webhook:

```bash
uv run python -m hal.cli simulate-inbound --chat-id "+15551234567" --text "hello"
```

It should run the same code path as the webhook after parsing.

### Step 7: Tighten Later

After v0 works:

- add a task queue if webhook latency is a problem
- add a turn ID to prevent duplicate sends
- add typing indicator support
- switch subprocess invocation to Agent SDK
- add typed MCP tools instead of Bash CLI

## Recommended V0 Decision

Use SQLite for chat memory. Use Blooio only for webhook ingress and SMS egress.

Use `halctl send-sms` as the first tool Claude Code can call. Store prompt/personality files on disk and log each agent run so prompt changes can be evaluated quickly.
