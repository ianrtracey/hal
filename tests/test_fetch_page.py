from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from hal.db import Database
from hal.tools import fetch_page as fp


ARTICLE_HTML = """
<!doctype html>
<html><head><title>The Test</title></head>
<body>
<nav>nav junk</nav>
<header>header junk</header>
<article>
<h1>The Test Headline</h1>
<p>This is a sufficiently long article body so that trafilatura will recognise
it as the main content and extract it cleanly. We need at least a couple
sentences here so the extractor has something substantive to lock onto.</p>
<p>Here is a second paragraph with even more useful prose. The point is to
clear whatever heuristic threshold trafilatura uses to decide what counts as
the primary article on the page.</p>
</article>
<footer>footer junk</footer>
</body></html>
"""


def _ctx(db):
    return SimpleNamespace(context=SimpleNamespace(db=db, chat_id="test-chat"))


def _patch_get(monkeypatch, response: httpx.Response):
    class StubClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return response

    monkeypatch.setattr(fp.httpx, "AsyncClient", StubClient)


def _patch_get_raises(monkeypatch, exc: Exception):
    class StubClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            raise exc

    monkeypatch.setattr(fp.httpx, "AsyncClient", StubClient)


def _patch_no_network(monkeypatch):
    class Boom:
        def __init__(self, *a, **kw):
            raise AssertionError("network must not be reached")

    monkeypatch.setattr(fp.httpx, "AsyncClient", Boom)


@pytest.mark.asyncio
async def test_happy_path(tmp_path, monkeypatch):
    db = Database(tmp_path / "hal.sqlite3")
    response = httpx.Response(
        200, text=ARTICLE_HTML, headers={"content-type": "text/html; charset=utf-8"}
    )
    _patch_get(monkeypatch, response)

    out = await fp._fetch_page_impl(_ctx(db), "https://example.com/article")

    assert "Test Headline" in out
    assert "nav junk" not in out
    assert "footer junk" not in out

    rows = list(db.connect().execute("SELECT status, bytes_returned, url FROM fetch_log"))
    assert len(rows) == 1
    assert rows[0]["status"] == "http_200"
    assert rows[0]["url"] == "https://example.com/article"
    assert rows[0]["bytes_returned"] > 0


@pytest.mark.asyncio
async def test_trafilatura_none_falls_back(tmp_path, monkeypatch):
    db = Database(tmp_path / "hal.sqlite3")
    response = httpx.Response(
        200,
        text="<html><body><p>raw fallback content here</p></body></html>",
        headers={"content-type": "text/html"},
    )
    _patch_get(monkeypatch, response)
    monkeypatch.setattr(fp.trafilatura, "extract", lambda *a, **kw: None)

    out = await fp._fetch_page_impl(_ctx(db), "https://example.com/empty")

    assert "raw fallback content here" in out


@pytest.mark.asyncio
async def test_truncation(tmp_path, monkeypatch):
    db = Database(tmp_path / "hal.sqlite3")
    body = "<html><body><article>" + ("word " * 5000) + "</article></body></html>"
    response = httpx.Response(200, text=body, headers={"content-type": "text/html"})
    _patch_get(monkeypatch, response)

    out = await fp._fetch_page_impl(_ctx(db), "https://example.com/long", max_chars=200)

    assert out.endswith("chars omitted]")
    assert "[truncated," in out
    assert len(out) < 300


@pytest.mark.asyncio
async def test_non_2xx(tmp_path, monkeypatch):
    db = Database(tmp_path / "hal.sqlite3")
    _patch_get(monkeypatch, httpx.Response(404, text="not found"))

    out = await fp._fetch_page_impl(_ctx(db), "https://example.com/missing")

    assert out == "http 404: https://example.com/missing"
    rows = list(db.connect().execute("SELECT status FROM fetch_log"))
    assert rows[0]["status"] == "http_404"


@pytest.mark.asyncio
async def test_http_error(tmp_path, monkeypatch):
    db = Database(tmp_path / "hal.sqlite3")
    _patch_get_raises(monkeypatch, httpx.ConnectTimeout("timed out"))

    out = await fp._fetch_page_impl(_ctx(db), "https://example.com/slow")

    assert out.startswith("error: ")
    rows = list(db.connect().execute("SELECT status FROM fetch_log"))
    assert rows[0]["status"] == "error"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/",
        "http://localhost/",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
        "https:///nohost",
    ],
)
@pytest.mark.asyncio
async def test_ssrf_blocklist(url, tmp_path, monkeypatch):
    db = Database(tmp_path / "hal.sqlite3")
    _patch_no_network(monkeypatch)

    out = await fp._fetch_page_impl(_ctx(db), url)

    assert out.startswith("refused:")
    rows = list(db.connect().execute("SELECT status FROM fetch_log"))
    assert rows[0]["status"] == "refused"
