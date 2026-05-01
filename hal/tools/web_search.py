from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx
from agents import RunContextWrapper, function_tool

logger = logging.getLogger("hal.tools.web_search")

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
DEFAULT_TIMEOUT = 5.0
DEFAULT_COUNT = 5
MAX_COUNT = 10
MAX_OUTPUT_CHARS = 3000

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(value: str | None) -> str:
    if not value:
        return ""
    return _TAG_RE.sub("", value).strip()


def _format_infobox(infobox: dict[str, Any] | None) -> str | None:
    if not isinstance(infobox, dict):
        return None
    long_desc = _strip_tags(infobox.get("long_desc") or infobox.get("description"))
    label = _strip_tags(infobox.get("label") or infobox.get("title"))
    if long_desc:
        return f"{label}: {long_desc}" if label else long_desc
    if label:
        return label
    return None


def _format_results(data: dict[str, Any]) -> tuple[str, int]:
    web = data.get("web") if isinstance(data, dict) else None
    results = web.get("results") if isinstance(web, dict) else None
    if not isinstance(results, list):
        results = []

    lines: list[str] = []
    infobox_line = _format_infobox(data.get("infobox") if isinstance(data, dict) else None)
    if infobox_line:
        lines.append(infobox_line)
        lines.append("")

    for i, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue
        title = _strip_tags(item.get("title")) or "(untitled)"
        url = item.get("url") or ""
        desc = _strip_tags(item.get("description"))
        lines.append(f"{i}. {title} — {url}")
        if desc:
            lines.append(f"   {desc}")

    return "\n".join(lines), len(results)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n\n[truncated]"


async def _web_search_impl(
    ctx: RunContextWrapper,
    query: str,
    count: int = DEFAULT_COUNT,
) -> str:
    db = ctx.context.db if ctx and ctx.context else None
    chat_id = ctx.context.chat_id if ctx and ctx.context else None
    api_key = ctx.context.settings.brave_api_key if ctx and ctx.context else None
    t0 = time.monotonic()

    def _log(status: str, result_count: int) -> None:
        if db is None:
            return
        try:
            db.record_search(
                chat_id=chat_id,
                query=query,
                status=status,
                result_count=result_count,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
        except Exception:
            logger.exception("record_search failed for %r", query)

    if not api_key:
        _log("unconfigured", 0)
        return "web search unavailable: not configured"

    count = max(1, min(count, MAX_COUNT))
    headers = {
        "X-Subscription-Token": api_key,
        "Accept": "application/json",
    }
    params = {"q": query, "count": count}

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            r = await client.get(BRAVE_ENDPOINT, params=params, headers=headers)
    except httpx.HTTPError as exc:
        _log("error", 0)
        return f"search error: {exc}"

    if r.status_code >= 400:
        _log(f"http_{r.status_code}", 0)
        return f"search error: http {r.status_code}"

    try:
        data = r.json()
    except ValueError:
        _log("invalid_json", 0)
        return "search error: invalid response"

    body, result_count = _format_results(data)
    if result_count == 0 and not body.strip():
        _log("empty", 0)
        return f"no results for: {query}"

    output = _truncate(body, MAX_OUTPUT_CHARS)
    _log(f"http_{r.status_code}", result_count)
    return output


web_search = function_tool(
    _web_search_impl,
    name_override="web_search",
    description_override=(
        "Search the web via Brave Search and return a compact list of results "
        "(title, URL, snippet). Use this when the user asks about current events, "
        "recent news, or facts you're not confident about. Summarize the results "
        "for SMS — don't paste them back. "
        "Before calling this tool, send a brief send_sms confirmation telling the "
        "user you're looking it up (e.g. 'looking that up…') so they aren't left "
        "wondering during the few-second search. "
        "If the first search misses, refine the query and search again — keep "
        "going until you have a real answer or you've concluded the web doesn't "
        "have one. Cap yourself at ~4 searches per turn. A second short send_sms "
        "('still looking…') is fine if you go more than a round or two. "
        "If the result is 'web search unavailable: ...', 'search error: ...', or "
        "'no results for: ...', tell the user briefly and stop — don't retry the "
        "same query."
    ),
)
