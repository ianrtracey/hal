# Web Search

## Overview

Give Hal the ability to search the web when the user asks about current
events, fresh facts, or anything outside the model's knowledge. The
agent gets a `web_search` tool that takes a query, hits the Brave Search
API, and returns a compact list of results (title, URL, snippet) the
model can summarize over SMS.

Approach: plain `httpx` against `api.search.brave.com`. No scraping, no
headless browser. Brave's snippets are usually enough for SMS-grade
answers; when they aren't, the agent can pair this with `fetch_page`
(see `web-fetch.md`) to drill into a specific result.

## Current State

- `hal/openai_agent.py` defines tools with `@function_tool` and registers
  them on the `Agent` at the bottom of `OpenAIAgentRunner.run_sms_turn()`
  (around line 332). Existing tools: `send_sms`, `react`, `record_note`,
  `remember_contact`, `remember_chat`, `create_group_chat`.
- All tools are `async`, take `RunContextWrapper[HalContext]`, and log
  their actions to SQLite via `ctx.context.db`.
- `requests>=2.33.1` is in main deps; `httpx>=0.28.1` is in dev only.
- No `hal/tools/` package yet. The `web-fetch.md` plan also creates this
  package — whichever lands first adds `hal/tools/__init__.py`.
- `BRAVE_API_KEY` is not yet wired into `hal/config.py`.

## Design

### New module: `hal/tools/web_search.py`

```python
import time
import httpx
from agents import RunContextWrapper, function_tool
from hal.openai_agent import HalContext  # or hal.context if extracted

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
DEFAULT_TIMEOUT = 5.0
DEFAULT_COUNT = 5
MAX_COUNT = 10
MAX_OUTPUT_CHARS = 3000

@function_tool
async def web_search(
    ctx: RunContextWrapper[HalContext],
    query: str,
    count: int = DEFAULT_COUNT,
) -> str:
    """Search the web and return a compact list of results.

    Use this when the user asks about current events, recent news, facts
    you're not confident about, or anything that needs fresh information.
    Each result includes title, URL, and a short snippet. Summarize for
    SMS — don't paste raw results back to the user.

    Args:
        query: Search query. Plain natural-language works best.
        count: Number of results to return (default 5, max 10).
    """
```

The body:

1. If `ctx.context.settings.brave_api_key` is missing, return
   `"web search unavailable: not configured"` without a network call.
2. `count = max(1, min(count, MAX_COUNT))`.
3. `async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
   r = await client.get(BRAVE_ENDPOINT, params={"q": query, "count": count},
   headers={"X-Subscription-Token": key, "Accept": "application/json"})`.
4. On non-2xx, return `f"search error: http {r.status_code}"`. Treat
   `429` the same way — Brave's free tier is 1 qps.
5. Parse `data["web"]["results"]` (may be empty or missing). Format
   each as:
   ```
   1. <title> — <url>
      <description>
   ```
   Strip Brave's `<strong>` highlight tags from titles and descriptions
   before formatting.
6. If `data` includes an `infobox` block, prepend a one-line summary
   from it (often high-signal for factual queries). Skip silently if
   missing or malformed.
7. If no results: return `f"no results for: {query}"`.
8. Truncate the assembled string to `MAX_OUTPUT_CHARS` and append
   `\n\n[truncated]` when we cut.
9. Log to SQLite (see audit log) and return the markdown.

### Config

Add to `hal/config.py`:

```python
brave_api_key: str | None = field(
    default_factory=lambda: os.environ.get("BRAVE_API_KEY")
)
```

Set the env var on the VPS via the existing `.env` mechanism.

### Audit log

Reuse whatever pattern `record_note` / `remember_contact` use for
action logging (check `hal/db.py`). Columns we need on every search:

- `chat_id`
- `query`
- `result_count`
- `status` (http status, or `"unconfigured"`, or `"error"`)
- `latency_ms`
- `created_at`

If the existing actions table doesn't fit cleanly, add a dedicated
`search_log` table. Decide once we read `hal/db.py`.

### Tool registration

In `hal/openai_agent.py`:

```python
from hal.tools.web_search import web_search

# ...

agent = Agent(
    name="Hal",
    instructions=instructions,
    tools=[send_sms, react, record_note, remember_contact, remember_chat,
           create_group_chat, web_search],
    model=model,
)
```

### Prompt update

Append to `prompts/system.md`:

> You have a `web_search(query, count=5)` tool that returns title, URL,
> and snippet for each result. Use it when the user asks about current
> events, recent news, or facts you're not confident about. Summarize
> the results in your reply — don't paste them back.
>
> **Send a confirmation first.** Before calling `web_search`, call
> `send_sms` with a brief acknowledgement of what you're looking up
> (e.g. "looking that up…", "checking the news on X…"). Search takes a
> few seconds and the user shouldn't be left wondering. One short
> confirmation, then the search, then the real answer.
>
> **Iterate if the first search misses.** If the top results don't
> actually answer the question, refine the query and search again.
> Keep going until you have a real answer or you've concluded the web
> doesn't have one — don't give up after one try, and don't fabricate
> from weak snippets. A second `send_sms` ("still looking…", "trying a
> different angle…") is fine if you're going more than one or two
> rounds. Cap yourself at ~4 searches per turn to avoid runaway loops.
>
> If the tool returns `web search unavailable: ...`, `search error: ...`,
> or `no results for: ...`, tell the user briefly and stop; don't retry
> the same query.

The tool docstring should mirror the confirmation and iteration rules
so the model sees them at tool-selection time, not just in the system
prompt.

## Steps

### 1. Add dependency

```bash
uv add httpx
```

`httpx` moves from dev to main. (Same step the `web-fetch.md` plan
needs — whichever lands first does it.)

### 2. Create `hal/tools/__init__.py` and `hal/tools/web_search.py`

`__init__.py` empty. `web_search.py` per the design above.

If `HalContext` isn't importable without a circular dep, do
`from hal.openai_agent import HalContext` inside the tool function, or
extract `HalContext` to `hal/context.py` as a small prerequisite refactor.
Decide after reading `openai_agent.py` — prefer the inline import if the
dataclass doesn't move cleanly.

### 3. Wire config

Add `brave_api_key` to `Settings` in `hal/config.py`. Add `BRAVE_API_KEY`
to `.env` on the VPS.

### 4. Wire audit logging

Read `hal/db.py`, decide whether to extend the existing actions table or
add `search_log`. Implement `db.record_search(...)` and call it from the
tool on every terminal path (success, http error, unconfigured, raised).

### 5. Register the tool and update the prompt

Edit `hal/openai_agent.py` tool list and `prompts/system.md` per the
design.

### 6. Tests

In `tests/test_web_search.py`:

- **Happy path**: monkeypatch `httpx.AsyncClient.get` to return a fixture
  JSON with three results; assert formatted output contains expected
  titles, URLs, and snippets, and that `<strong>` tags are stripped.
- **Missing key**: clear `BRAVE_API_KEY`; assert returns
  `"web search unavailable: not configured"` and no network call is
  attempted.
- **Non-2xx**: monkeypatch the client to return 429 and 500; assert
  output is `"search error: http 429"` / `"search error: http 500"`.
- **Empty results**: fixture with `web.results: []`; assert returns
  `"no results for: <query>"`.
- **Truncation**: fixture with many long results; assert output ends
  with `[truncated]` and length is bounded.
- **Count clamp**: pass `count=999`; assert outbound request uses
  `count=10`.

### 7. Deploy

rsync to VPS, set `BRAVE_API_KEY` in `.env`, `uv sync`, restart in the
`hal` tmux session per `CLAUDE.md`. Manually text Hal a question that
needs fresh information ("what's the latest on X?") and verify it
returns a coherent summary rather than dumping raw results.

## Open Questions

1. **Snippets vs. fetch chain.** Brave's snippets are often enough for
   SMS answers. When they're not, the agent can pass a result URL to
   `fetch_page` (when that lands). Ship `web_search` standalone first
   and see how often the snippets fall short before formalizing the
   chain in the prompt.
2. **Result count default.** 5 keeps context cost tiny and
   SMS-summarizable. Agent can request more per call. Revisit if 5 is
   consistently too few.
3. **Other Brave response sections.** `news`, `videos`, `infobox`, `faq`
   — including `infobox` is high-value for factual queries (capitals,
   definitions, people). v1 includes infobox only; add others if we see
   queries that obviously want them.
4. **Caching.** Free tier is 1 qps / 2000 queries/month. Single-user
   volume won't come close. Skip caching for v1, revisit if the audit
   log shows repeats.
5. **Freshness / locale params.** Brave supports `freshness` (pd/pw/pm/py)
   and `country`/`search_lang`. Skip in v1 — let the model put time
   words in the query. Add typed params if we see the model struggling
   with recency.
6. **Safe search.** Default Brave behavior is moderate. Leave it; not
   worth a knob until there's a reason.
