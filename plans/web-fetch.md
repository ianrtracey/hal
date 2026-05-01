# Web Page Fetch

## Overview

Give Hal the ability to read a web page when the user mentions a URL or asks
for information that's only on the web. The agent gets a `fetch_page` tool
that takes a URL, fetches the page over HTTP, extracts the readable
content, and returns markdown the model can summarize over SMS.

Approach: plain `httpx` for the network call + `trafilatura` for
boilerplate-stripping and markdown extraction. No headless browser, no
hosted scraping API. Works for static articles, blog posts, docs, and
most server-rendered pages — fails on JS-only SPAs, which we accept as
the tradeoff for keeping the stack light and the supervisor/rollback
guarantees intact.

## Current State

- `hal/openai_agent.py` defines tools with `@function_tool` and registers
  them on the `Agent` at the bottom of `OpenAIAgentRunner.run_sms_turn()`
  (around line 332). Existing tools: `send_sms`, `react`, `record_note`,
  `remember_contact`, `remember_chat`, `create_group_chat`.
- All tools are `async`, take `RunContextWrapper[HalContext]`, and log
  their actions to SQLite via `ctx.context.db`.
- `requests>=2.33.1` is in main deps; `httpx>=0.28.1` is in dev only.
- No HTML parsing libraries installed.
- No `tools/` package — every tool currently lives in `openai_agent.py`.
  This plan creates `hal/tools/` and moves `fetch_page` there as the
  first tenant; existing tools stay where they are for now.

## Design

### New module: `hal/tools/fetch_page.py`

```python
import time
import httpx
import trafilatura
from agents import RunContextWrapper, function_tool
from hal.context import HalContext  # if HalContext gets extracted; otherwise import from openai_agent

USER_AGENT = "HalBot/1.0 (+https://ians-hal.duckdns.org)"
DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_CHARS = 8000

@function_tool
async def fetch_page(
    ctx: RunContextWrapper[HalContext],
    url: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Fetch a web page and return readable markdown.

    Use this when the user references a URL or asks for information from
    a specific page. Output is truncated to roughly `max_chars` characters;
    summarize for SMS rather than dumping the page back to the user.

    Args:
        url: Absolute http(s) URL.
        max_chars: Soft cap on returned characters. Default 8000.
    """
```

The body:
1. Validate URL (see SSRF guard below). On reject, return a short
   `"refused: <reason>"` string — let the agent decide what to say.
2. `async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client: r = await client.get(url)`.
3. On non-2xx, return `f"http {r.status_code}: {url}"`.
4. Hand `r.text` to `trafilatura.extract(html, output_format="markdown", include_links=True)`.
5. If trafilatura returns `None` (no main content found), fall back to a
   minimal text extraction: strip tags via a small helper, collapse
   whitespace. Better something than nothing.
6. Truncate to `max_chars` and append `\n\n[truncated, N chars omitted]`
   when we cut.
7. Log the call to SQLite (see below) and return the markdown.

### SSRF guard

A small helper, `_is_safe_url(url) -> tuple[bool, str]`, run before any
network call. Reject:

- Any scheme other than `http` / `https`.
- Hosts that resolve to `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`, `169.254.0.0/16`, `::1`, `fc00::/7`.
- Literal hostnames `localhost`, `metadata.google.internal`, the AWS
  IMDS IP `169.254.169.254`.

We resolve the hostname with `socket.getaddrinfo` and check every
returned address — a domain that resolves to a private IP still gets
blocked. Return `(False, reason)` so the tool can include the reason
in its refusal string for debugging.

### Audit log

Add a `fetch_page` action type to whatever existing actions table
`record_note` / `remember_contact` write to (check the schema in
`hal/db.py`). Columns we need on every fetch:

- `chat_id`
- `url`
- `status` (http status, or `"refused"`, or `"error"`)
- `bytes_returned`
- `latency_ms`
- `created_at`

If the existing actions table doesn't fit cleanly, add a dedicated
`fetch_log` table. Decide once we read `hal/db.py`.

### Tool registration

In `hal/openai_agent.py`:

```python
from hal.tools.fetch_page import fetch_page

# ...

agent = Agent(
    name="Hal",
    instructions=instructions,
    tools=[send_sms, react, record_note, remember_contact, remember_chat,
           create_group_chat, fetch_page],
    model=model,
)
```

### Prompt update

Append to `prompts/system.md`:

> You have a `fetch_page(url, max_chars=8000)` tool that returns the
> readable markdown of a web page. Use it when the user shares a URL or
> asks about content that lives on a specific page. The output is
> truncated — summarize the page for SMS, don't paste it back. If the
> tool returns `refused: ...` or `http 4xx/5xx`, tell the user briefly
> and stop; don't retry the same URL.

## Steps

### 1. Add dependencies

```bash
uv add httpx trafilatura
```

`httpx` moves from dev to main. `trafilatura` is new; pulls in `lxml`
and `justext` as transitive deps — confirm they install cleanly on the
VPS (Debian wheels are fine, no compile step expected).

### 2. Create `hal/tools/__init__.py` and `hal/tools/fetch_page.py`

`__init__.py` empty for now. `fetch_page.py` per the design above.

If `HalContext` is currently defined inside `openai_agent.py` and not
importable without a circular dep, accept the cycle by doing the
`from hal.openai_agent import HalContext` import inside the tool
function, or extract `HalContext` to `hal/context.py` as a small
prerequisite refactor. Decide after reading `openai_agent.py` — prefer
the import-inside-function fix if the dataclass doesn't move cleanly.

### 3. Add SSRF guard

Inline in `fetch_page.py` for now. If a second tool needs it later,
promote to `hal/tools/_net.py`.

### 4. Wire audit logging

Read `hal/db.py`, decide whether to extend the existing actions table
or add `fetch_log`. Implement `db.record_fetch(...)` and call it from
the tool on every terminal path (success, http error, refused, raised).

### 5. Register the tool and update the prompt

Edit `hal/openai_agent.py` tool list and `prompts/system.md` per the
design.

### 6. Tests

In `tests/test_fetch_page.py`:

- **Happy path**: monkeypatch `httpx.AsyncClient.get` to return a known
  HTML fixture; assert markdown contains expected article text and no
  `<script>`/nav boilerplate.
- **trafilatura returns None**: feed it HTML with no extractable main
  content; assert we still return *something* (fallback path), not an
  exception.
- **Truncation**: feed a long article; assert output ends with the
  `[truncated, N chars omitted]` marker and length is within bounds.
- **SSRF blocklist**: parametrize over `http://localhost`,
  `http://127.0.0.1`, `http://10.0.0.1`, `http://169.254.169.254`,
  `file:///etc/passwd`, `ftp://example.com`; assert each returns
  `refused: ...` without a network call (mock `getaddrinfo`).
- **Non-2xx**: monkeypatch the client to return 404; assert
  `"http 404"` in the result.

### 7. Deploy

rsync to VPS, `uv sync`, restart in the `hal` tmux session per
`CLAUDE.md`. Manually text Hal a URL (e.g. a blog post link) and
verify it summarizes rather than dumps.

## Open Questions

1. **Caching.** Worth a small SQLite cache keyed on `(url, date)` so
   re-asking about the same article is free? Useful but adds surface
   area. **Skip for v1**, revisit if we see repeat fetches in the
   audit log.
2. **JS-rendered pages.** When trafilatura returns `None` on an SPA,
   v1 falls back to raw text strip and the agent will likely produce a
   bad summary. Acceptable failure mode for v1 — if it bites in
   practice, add Jina Reader as a second-stage fallback (we already
   considered it). Don't reach for Playwright unless we have a
   concrete reason.
3. **Authenticated pages / paywalls.** Out of scope. No cookie jar, no
   session reuse.
4. **Rate limiting.** A determined agent loop could hammer a site. Not
   a real risk at single-user volume but worth a TODO: a simple
   per-host minimum interval (e.g. 1s) inside the tool, gated on the
   audit log.
5. **`max_chars` default.** 8000 chars ≈ 2k tokens — generous for
   summarization, small enough not to dominate the context. Tunable
   per-call by the agent. Revisit if we hit context pressure.
6. **PDFs and non-HTML content types.** v1 only handles
   `text/html`. If `Content-Type` isn't HTML, return
   `"unsupported content-type: <type>"`. PDF support is a separate
   tool if we want it.
