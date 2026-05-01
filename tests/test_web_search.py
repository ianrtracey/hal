from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from hal.db import Database
from hal.tools import web_search as ws


def _ctx(db, *, brave_api_key: str | None = "test-key"):
    return SimpleNamespace(
        context=SimpleNamespace(
            db=db,
            chat_id="test-chat",
            settings=SimpleNamespace(brave_api_key=brave_api_key),
        )
    )


def _patch_get(monkeypatch, response: httpx.Response, captured: dict | None = None):
    class StubClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            if captured is not None:
                captured["url"] = url
                captured["params"] = params
                captured["headers"] = headers
            return response

    monkeypatch.setattr(ws.httpx, "AsyncClient", StubClient)


def _patch_no_network(monkeypatch):
    class Boom:
        def __init__(self, *a, **kw):
            raise AssertionError("network must not be reached")

    monkeypatch.setattr(ws.httpx, "AsyncClient", Boom)


SAMPLE_RESPONSE = {
    "web": {
        "results": [
            {
                "title": "First <strong>result</strong>",
                "url": "https://example.com/one",
                "description": "An <strong>introductory</strong> snippet.",
            },
            {
                "title": "Second result",
                "url": "https://example.com/two",
                "description": "More details here.",
            },
            {
                "title": "Third",
                "url": "https://example.com/three",
                "description": "",
            },
        ]
    }
}


@pytest.mark.asyncio
async def test_happy_path(tmp_path, monkeypatch):
    db = Database(tmp_path / "hal.sqlite3")
    captured: dict = {}
    _patch_get(monkeypatch, httpx.Response(200, json=SAMPLE_RESPONSE), captured)

    out = await ws._web_search_impl(_ctx(db), "what is brave search")

    assert "1. First result — https://example.com/one" in out
    assert "An introductory snippet." in out
    assert "<strong>" not in out
    assert "2. Second result — https://example.com/two" in out
    assert "3. Third — https://example.com/three" in out

    assert captured["params"] == {"q": "what is brave search", "count": 5}
    assert captured["headers"]["X-Subscription-Token"] == "test-key"

    rows = list(db.connect().execute("SELECT status, result_count, query FROM search_log"))
    assert len(rows) == 1
    assert rows[0]["status"] == "http_200"
    assert rows[0]["result_count"] == 3
    assert rows[0]["query"] == "what is brave search"


@pytest.mark.asyncio
async def test_missing_key_skips_network(tmp_path, monkeypatch):
    db = Database(tmp_path / "hal.sqlite3")
    _patch_no_network(monkeypatch)

    out = await ws._web_search_impl(_ctx(db, brave_api_key=None), "anything")

    assert out == "web search unavailable: not configured"
    rows = list(db.connect().execute("SELECT status FROM search_log"))
    assert rows[0]["status"] == "unconfigured"


@pytest.mark.parametrize("status_code", [429, 500, 503])
@pytest.mark.asyncio
async def test_non_2xx(status_code, tmp_path, monkeypatch):
    db = Database(tmp_path / "hal.sqlite3")
    _patch_get(monkeypatch, httpx.Response(status_code, text="nope"))

    out = await ws._web_search_impl(_ctx(db), "x")

    assert out == f"search error: http {status_code}"
    rows = list(db.connect().execute("SELECT status FROM search_log"))
    assert rows[0]["status"] == f"http_{status_code}"


@pytest.mark.asyncio
async def test_empty_results(tmp_path, monkeypatch):
    db = Database(tmp_path / "hal.sqlite3")
    _patch_get(monkeypatch, httpx.Response(200, json={"web": {"results": []}}))

    out = await ws._web_search_impl(_ctx(db), "obscure query")

    assert out == "no results for: obscure query"
    rows = list(db.connect().execute("SELECT status, result_count FROM search_log"))
    assert rows[0]["status"] == "empty"
    assert rows[0]["result_count"] == 0


@pytest.mark.asyncio
async def test_truncation(tmp_path, monkeypatch):
    db = Database(tmp_path / "hal.sqlite3")
    long_desc = "x " * 5000
    payload = {
        "web": {
            "results": [
                {
                    "title": f"Result {i}",
                    "url": f"https://example.com/{i}",
                    "description": long_desc,
                }
                for i in range(10)
            ]
        }
    }
    _patch_get(monkeypatch, httpx.Response(200, json=payload))

    out = await ws._web_search_impl(_ctx(db), "long")

    assert out.endswith("[truncated]")
    assert len(out) <= ws.MAX_OUTPUT_CHARS + len("\n\n[truncated]")


@pytest.mark.asyncio
async def test_count_clamped(tmp_path, monkeypatch):
    db = Database(tmp_path / "hal.sqlite3")
    captured: dict = {}
    _patch_get(monkeypatch, httpx.Response(200, json=SAMPLE_RESPONSE), captured)

    await ws._web_search_impl(_ctx(db), "x", count=999)
    assert captured["params"]["count"] == ws.MAX_COUNT

    captured.clear()
    _patch_get(monkeypatch, httpx.Response(200, json=SAMPLE_RESPONSE), captured)
    await ws._web_search_impl(_ctx(db), "x", count=0)
    assert captured["params"]["count"] == 1


@pytest.mark.asyncio
async def test_infobox_prepended(tmp_path, monkeypatch):
    db = Database(tmp_path / "hal.sqlite3")
    payload = {
        "infobox": {
            "label": "Albert Einstein",
            "long_desc": "German-born theoretical physicist.",
        },
        "web": {
            "results": [
                {"title": "Bio", "url": "https://example.com/bio", "description": "..."}
            ]
        },
    }
    _patch_get(monkeypatch, httpx.Response(200, json=payload))

    out = await ws._web_search_impl(_ctx(db), "einstein")

    assert out.startswith("Albert Einstein: German-born theoretical physicist.")
    assert "1. Bio — https://example.com/bio" in out


@pytest.mark.asyncio
async def test_http_error(tmp_path, monkeypatch):
    db = Database(tmp_path / "hal.sqlite3")

    class StubClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(ws.httpx, "AsyncClient", StubClient)

    out = await ws._web_search_impl(_ctx(db), "x")

    assert out.startswith("search error: ")
    rows = list(db.connect().execute("SELECT status FROM search_log"))
    assert rows[0]["status"] == "error"
