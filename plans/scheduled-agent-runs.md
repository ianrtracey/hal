# Scheduled Agent Runs

## Overview

Allow Hal to schedule future agent runs — e.g. "remind me tomorrow at 9am to send X." The agent gets a `schedule_run` tool it can call during conversation. At the scheduled time, APScheduler fires the job, which runs the agent with the stored prompt and sends the result back to the chat.

## Current State

- `hal/scheduler.py` exists with a basic `BackgroundScheduler` + heartbeat job
- Scheduler uses in-memory job storage (jobs lost on restart)
- `hal/openai_agent.py` has the `OpenAIAgentRunner` with tools: `send_sms`, `react`, `record_note`, `remember_contact`, `remember_chat`
- Agent is initialized per-turn in `OpenAIAgentRunner.run_sms_turn()`
- Scheduler is started in `app.py` lifespan and stored on `app.state.scheduler`

## Design

### Job persistence

Switch from in-memory to SQLAlchemy jobstore backed by the existing SQLite database (`var/hal.sqlite3`). This means scheduled jobs survive restarts and deploys.

```python
# hal/scheduler.py
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

jobstores = {
    "default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}")
}
scheduler = BackgroundScheduler(jobstores=jobstores, timezone="UTC")
```

### New agent tool: `schedule_run`

Add a `schedule_run` tool to `hal/openai_agent.py` that the agent can call:

```python
@function_tool
async def schedule_run(
    ctx: RunContextWrapper[HalContext],
    run_at_iso: str,
    prompt: str,
) -> str:
    """Schedule a future agent run. Use this for reminders, follow-ups, or
    any task the user wants done at a specific time.

    Args:
        run_at_iso: ISO 8601 datetime string in UTC (e.g. "2026-04-25T13:00:00Z")
        prompt: The instruction for the agent when it runs (e.g. "Remind Ian to call the dentist")
    """
```

This tool needs access to the scheduler instance. Two options:

**Option A: Add scheduler to `HalContext`** (simpler)
- Add `scheduler: BackgroundScheduler | None` field to `HalContext`
- Pass it through from `OpenAIAgentRunner` which gets it at init time
- The tool calls `ctx.context.scheduler.add_job(...)`

**Option B: Module-level scheduler singleton**
- `hal/scheduler.py` exports a module-level `get_scheduler()` that returns the running instance
- Tool imports and calls it directly

**Recommend Option A** — keeps dependencies explicit, easier to test.

### Scheduled job execution

When the job fires, it needs to:
1. Run the agent with the stored prompt against the stored chat_id
2. The agent has full tool access (send_sms, react, etc.)
3. No inbound message triggers it — it's a self-initiated turn

```python
def execute_scheduled_run(chat_id: str, prompt: str, settings_dict: dict):
    """Called by APScheduler at the scheduled time."""
    settings = Settings(**settings_dict)  # or reconstruct from env
    db = Database(settings.db_path)
    runner = OpenAIAgentRunner(settings, db)
    # Run synchronously since APScheduler's BackgroundScheduler
    # uses a thread pool, not an async loop
    import asyncio
    result = asyncio.run(runner.run_scheduled_turn(chat_id, prompt))
```

Need a new method on `OpenAIAgentRunner`:

```python
async def run_scheduled_turn(self, chat_id: str, prompt: str) -> AgentRunResult:
    """Run a scheduled (unprompted) agent turn."""
    # Similar to run_sms_turn but:
    # - No inbound_message_id (self-initiated)
    # - prompt comes from the scheduled job, not an SMS
    # - Still loads transcript + contact notes for context
```

### Wiring it up

1. `hal/scheduler.py` — upgrade to persistent jobstore, export `add_scheduled_run()` helper
2. `hal/openai_agent.py` — add `schedule_run` tool, add `run_scheduled_turn()` method, add `scheduler` to `HalContext`
3. `hal/app.py` — pass scheduler into `HalService` so it can pass it to the agent runner
4. `hal/service.py` — thread scheduler through to `OpenAIAgentRunner`

### Timezone handling

The agent should parse natural language times relative to the user's timezone (America/New_York based on existing code in `openai_agent.py:275`). The system prompt already includes current date/time in ET. The tool should accept an ISO 8601 UTC string — the agent converts from the user's "tomorrow at 9am" to UTC before calling the tool.

Add to the agent instructions (in `prompts/system.md` or inline):
> When scheduling, convert times to UTC ISO 8601. The user is in America/New_York.

## Steps

### 1. Add `apscheduler[sqlalchemy]` dependency
```bash
uv add 'apscheduler[sqlalchemy]'
```
APScheduler 3.x includes SQLAlchemy jobstore. Check if `sqlalchemy` is already a transitive dep; if not, it'll be pulled in.

### 2. Upgrade `hal/scheduler.py`
- Switch to `SQLAlchemyJobStore` using the sqlite DB path
- Keep the heartbeat job
- Add `add_scheduled_run(scheduler, chat_id, run_at, prompt)` function
- Add `execute_scheduled_run(chat_id, prompt)` function that reconstructs the agent and runs it

### 3. Add `schedule_run` tool to `hal/openai_agent.py`
- Add `scheduler` field to `HalContext` (type: `BackgroundScheduler | None`)
- Create `schedule_run` function tool
- Add it to the agent's tool list
- Add `run_scheduled_turn()` method to `OpenAIAgentRunner`

### 4. Thread scheduler through `app.py` → `service.py` → `openai_agent.py`
- `app.py` lifespan: pass `scheduler` to `HalService`
- `HalService.__init__`: accept and store scheduler, pass to `OpenAIAgentRunner`
- `OpenAIAgentRunner.__init__`: accept and store scheduler
- `OpenAIAgentRunner.run_sms_turn`: pass scheduler into `HalContext`

### 5. Update agent instructions
- Add a line to system prompt about the `schedule_run` tool and timezone expectations
- Agent should confirm the scheduled time back to the user

### 6. Test locally
- Schedule a run 1 minute in the future
- Verify it fires and sends the SMS
- Restart the server and verify the job survives

### 7. Deploy
- rsync to VPS, `uv sync` to install new deps, restart service

## Open Questions

1. **APScheduler 3.x vs 4.x?** — The project currently uses `BackgroundScheduler` from APScheduler 3.x. v4 has a different API (async-native). Stick with 3.x since it's already working.
2. **Job serialization** — APScheduler's SQLAlchemy store pickles job functions. `execute_scheduled_run` must be a top-level importable function (not a lambda/closure) for this to work. Plan for this.
3. **Missed jobs on restart** — APScheduler has `misfire_grace_time` config. Set a generous window (e.g. 3600s) so jobs that fire while the server is down still execute on restart.
4. **Cancellation** — Should the agent be able to cancel scheduled runs? Could add a `cancel_scheduled_run` tool later, but not needed for v1.
5. **Listing scheduled jobs** — Nice to have: a tool or admin endpoint to list pending jobs. Not v1.
