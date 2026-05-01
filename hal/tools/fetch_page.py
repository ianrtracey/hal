from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import time
from urllib.parse import urlparse

import httpx
import trafilatura
from agents import RunContextWrapper, function_tool

logger = logging.getLogger("hal.tools.fetch_page")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 HalBot/1.0"
)
DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_CHARS = 8000

_BLOCKED_HOSTS = {"localhost", "metadata.google.internal"}
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _is_safe_url(url: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False, f"unsupported scheme: {parsed.scheme or '(none)'}"
    host = parsed.hostname
    if not host:
        return False, "missing host"
    if host.lower() in _BLOCKED_HOSTS:
        return False, f"blocked host: {host}"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return False, f"dns error: {exc}"
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False, f"blocked address: {addr}"
    return True, ""


def _fallback_text(html: str) -> str:
    no_tags = _TAG_RE.sub(" ", html)
    return _WS_RE.sub(" ", no_tags).strip()


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return text[:max_chars] + f"\n\n[truncated, {omitted} chars omitted]"


async def _fetch_page_impl(
    ctx: RunContextWrapper,
    url: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    db = ctx.context.db if ctx and ctx.context else None
    chat_id = ctx.context.chat_id if ctx and ctx.context else None
    t0 = time.monotonic()

    def _log(status: str, body: str) -> None:
        if db is None:
            return
        try:
            db.record_fetch(
                chat_id=chat_id,
                url=url,
                status=status,
                bytes_returned=len(body),
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
        except Exception:
            logger.exception("record_fetch failed for %s", url)

    safe, reason = await asyncio.to_thread(_is_safe_url, url)
    if not safe:
        result = f"refused: {reason}"
        _log("refused", result)
        return result

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers=headers,
        ) as client:
            r = await client.get(url)
    except httpx.HTTPError as exc:
        result = f"error: {exc}"
        _log("error", result)
        return result

    if r.status_code >= 400:
        result = f"http {r.status_code}: {url}"
        _log(f"http_{r.status_code}", "")
        return result

    content_type = (r.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if content_type and not (content_type.startswith("text/html") or content_type.endswith("+xml") or content_type == "application/xhtml+xml"):
        result = f"unsupported content-type: {content_type}"
        _log("unsupported_content_type", "")
        return result

    html = r.text
    extracted = await asyncio.to_thread(
        trafilatura.extract,
        html,
        output_format="markdown",
        include_links=True,
        include_comments=False,
        include_tables=True,
    )
    if not extracted:
        extracted = await asyncio.to_thread(
            trafilatura.extract,
            html,
            output_format="markdown",
            include_links=True,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
    if not extracted:
        extracted = _fallback_text(html)

    body = _truncate(extracted, max_chars)
    _log(f"http_{r.status_code}", body)
    return body


fetch_page = function_tool(
    _fetch_page_impl,
    name_override="fetch_page",
    description_override=(
        "Fetch a web page and return its readable content as markdown. "
        "Use this when the user shares a URL or asks about content that lives "
        "on a specific page. Output is truncated to roughly max_chars; "
        "summarize for SMS rather than dumping the page back to the user. "
        "If the result starts with 'refused:' or 'http 4xx/5xx', tell the user "
        "briefly and stop — do not retry the same URL."
    ),
)
