# Hal — Personal AI Assistant

## What it is

A personal AI assistant that communicates via SMS (Blooio), runs locally, and can modify and improve itself over time.

**Repo:** https://github.com/ianrtracey/hal

## What exists today

- **Blooio client** (`blooio_client.py`) — Python client with:
  - Bearer token auth via `.env`
  - `send_message(chat_id, text)` — send SMS
  - `start_typing()` / `stop_typing()` — typing indicators
  - `typing()` context manager — show typing bubble during processing
- **uv project** — dependency management via `pyproject.toml`
- Tested and working against Blooio's v2 API

## What we want to build

### Core App (FastAPI)

A FastAPI server that acts as the brain:

- **Inbound SMS webhook** — receive messages from Blooio, process them with an LLM, reply via the existing Blooio client (with typing indicators while thinking)
- **Cron jobs** — scheduled tasks (reminders, daily summaries, periodic checks) via APScheduler or similar
- **SQLite database** — conversation history, user preferences, task state, error logs
- **Local file access** — read/write files on the host machine
- **LLM integration** — call Claude (or other model) to generate responses and execute tasks

### Self-Modifying Supervisor Architecture

The key design goal: Hal should be able to modify its own code, restart itself, and debug its own failures.

#### Two-process model

```
┌─────────────────────────────┐
│  supervisor (simple, dumb)  │  ← never modified by the bot
│  - starts/stops the app     │
│  - captures stderr/crashes  │
│  - exposes restart trigger  │
│  - health checks            │
│  - rollback on failure      │
└─────────┬───────────────────┘
          │ spawns & monitors
┌─────────▼───────────────────┐
│  hal (FastAPI app)          │  ← the bot CAN modify this
│  - blooio integration       │
│  - llm logic, cron, etc.    │
│  - writes its own code      │
└─────────────────────────────┘
```

#### Supervisor responsibilities

- Spawn the FastAPI process (`uvicorn`)
- Restart on crash or on request (via signal or local socket/file)
- Capture stdout/stderr to a log file the bot can read
- After restart, ping `/health` — if it fails within N seconds, roll back
- Never modified by the bot itself (~50 lines, static)

#### Self-modification loop

1. Bot receives a task (via SMS or internally)
2. Bot uses an LLM to write/edit its own Python files on disk
3. Bot runs a validation step (syntax check, tests)
4. If it passes, bot signals the supervisor to restart
5. Supervisor kills old process, starts new one
6. If new process crashes or fails health check, supervisor reverts (via git) and restarts the old version
7. Bot reads crash logs from SQLite and can retry with fixes

#### Design rules

- **Supervisor is sacred** — the bot never touches it
- **Git as rollback** — commit before each self-edit, `git revert` if the new version fails
- **Health check** — supervisor validates the new version is alive before considering the deploy successful
- **Structured error capture** — crashes logged to SQLite so the bot can query its own failure history

## Tech stack

- **Python 3.12+** with uv for dependency management
- **FastAPI** — async HTTP server, webhook receiver
- **SQLite** — local database (conversation history, error logs, task state)
- **Blooio v2 API** — SMS send/receive, typing indicators, reactions
- **Claude API** — LLM for response generation and self-modification
- **APScheduler** (or similar) — cron jobs within the FastAPI process

## Open questions

- What guardrails should exist around self-modification? (e.g., which files/directories are off-limits beyond the supervisor?)
- Should the supervisor be a Python script or a bash script? (Python gives more control over health checks and rollback; bash is simpler and harder to accidentally break)
- How should the bot handle multi-step self-modifications that require dependency changes (new pip packages)?
- Should there be a human-approval step (via SMS confirmation) before self-modifications are applied?
- How to handle long-running LLM calls that outlive a restart cycle?
- What's the right persistence strategy for conversation context across restarts?
