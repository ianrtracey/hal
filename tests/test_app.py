from __future__ import annotations

import hmac
import json
import time
from hashlib import sha256

from fastapi.testclient import TestClient

from hal.app import create_app
from hal.db import Database


def test_health(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL_DB_PATH", str(tmp_path / "hal.sqlite3"))
    monkeypatch.setenv("HAL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HAL_SCHEDULER_ENABLED", "0")
    monkeypatch.delenv("BLOOIO_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HAL_WEBHOOK_TOKEN", raising=False)
    monkeypatch.delenv("HAL_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("BLOOIO_WEBHOOK_SECRET", raising=False)

    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_blooio_webhook_records_conversation(tmp_path, monkeypatch):
    db_path = tmp_path / "hal.sqlite3"
    monkeypatch.setenv("HAL_DB_PATH", str(db_path))
    monkeypatch.setenv("HAL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HAL_SCHEDULER_ENABLED", "0")
    monkeypatch.delenv("BLOOIO_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HAL_WEBHOOK_TOKEN", raising=False)
    monkeypatch.delenv("HAL_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("BLOOIO_WEBHOOK_SECRET", raising=False)

    with TestClient(create_app()) as client:
        response = client.post(
            "/webhooks/blooio",
            json={"chat_id": "+15551234567", "text": "hello"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["chat_id"] == "+15551234567"
    assert body["sent"] is False
    assert body["reply"] == "I received: hello"

    rows = Database(db_path).get_recent_messages("+15551234567", limit=10)
    assert [row["direction"] for row in rows] == ["inbound", "outbound"]
    webhooks = Database(db_path).list_webhooks(limit=10)
    assert len(webhooks) == 1
    assert webhooks[0]["chat_id"] == "+15551234567"
    assert webhooks[0]["text"] == "hello"
    assert webhooks[0]["status"] == "processed"


def test_blooio_webhook_accepts_received_message_payload(tmp_path, monkeypatch):
    db_path = tmp_path / "hal.sqlite3"
    monkeypatch.setenv("HAL_DB_PATH", str(db_path))
    monkeypatch.setenv("HAL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HAL_SCHEDULER_ENABLED", "0")
    monkeypatch.delenv("BLOOIO_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HAL_WEBHOOK_TOKEN", raising=False)
    monkeypatch.delenv("HAL_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("BLOOIO_WEBHOOK_SECRET", raising=False)

    payload = {
        "event": "message.received",
        "message_id": "-example",
        "external_id": "+16238662766",
        "protocol": "imessage",
        "timestamp": 1776572060036,
        "internal_id": "+17147100137",
        "is_group": False,
        "text": "Up",
        "sender": "+16238662766",
        "received_at": 1776572057818,
    }

    with TestClient(create_app()) as client:
        response = client.post(
            "/webhooks/blooio",
            json=payload,
            headers={"X-Blooio-Event": "message.received"},
        )

    assert response.status_code == 200
    assert response.json()["chat_id"] == "+16238662766"
    rows = Database(db_path).get_recent_messages("+16238662766", limit=10)
    assert [row["direction"] for row in rows] == ["inbound", "outbound"]
    webhooks = Database(db_path).list_webhooks(limit=10)
    assert webhooks[0]["event_id"] == "-example"
    assert webhooks[0]["chat_id"] == "+16238662766"
    assert webhooks[0]["text"] == "Up"
    assert webhooks[0]["status"] == "processed"


def test_blooio_webhook_ignores_non_received_events(tmp_path, monkeypatch):
    db_path = tmp_path / "hal.sqlite3"
    monkeypatch.setenv("HAL_DB_PATH", str(db_path))
    monkeypatch.setenv("HAL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HAL_SCHEDULER_ENABLED", "0")
    monkeypatch.delenv("BLOOIO_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HAL_WEBHOOK_TOKEN", raising=False)
    monkeypatch.delenv("HAL_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("BLOOIO_WEBHOOK_SECRET", raising=False)

    payload = {
        "event": "message.sent",
        "message_id": "-outbound",
        "sender": "+16238662766",
        "text": "I received: Up",
    }

    with TestClient(create_app()) as client:
        response = client.post(
            "/webhooks/blooio",
            json=payload,
            headers={"X-Blooio-Event": "message.sent"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert response.json()["event_type"] == "message.sent"
    assert Database(db_path).get_recent_messages("+16238662766", limit=10) == []
    webhooks = Database(db_path).list_webhooks(limit=10)
    assert webhooks[0]["event_type"] == "message.sent"
    assert webhooks[0]["status"] == "ignored"


def test_blooio_webhook_dedupes_event_id(tmp_path, monkeypatch):
    db_path = tmp_path / "hal.sqlite3"
    monkeypatch.setenv("HAL_DB_PATH", str(db_path))
    monkeypatch.setenv("HAL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HAL_SCHEDULER_ENABLED", "0")
    monkeypatch.delenv("BLOOIO_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HAL_WEBHOOK_TOKEN", raising=False)
    monkeypatch.delenv("HAL_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("BLOOIO_WEBHOOK_SECRET", raising=False)

    payload = {
        "event": "message.received",
        "message_id": "-same",
        "sender": "+16238662766",
        "text": "dedupe",
    }

    with TestClient(create_app()) as client:
        first = client.post("/webhooks/blooio", json=payload)
        second = client.post("/webhooks/blooio", json=payload)

    assert first.status_code == 200
    assert first.json()["reply"] == "I received: dedupe"
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"

    rows = Database(db_path).get_recent_messages("+16238662766", limit=10)
    assert [row["direction"] for row in rows] == ["inbound", "outbound"]
    webhooks = Database(db_path).list_webhooks(limit=10)
    assert [row["status"] for row in webhooks] == ["duplicate", "processed"]


def test_admin_restart_requires_token_and_writes_signal(tmp_path, monkeypatch):
    restart_signal = tmp_path / "restart.request"
    monkeypatch.setenv("HAL_DB_PATH", str(tmp_path / "hal.sqlite3"))
    monkeypatch.setenv("HAL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HAL_RESTART_SIGNAL_PATH", str(restart_signal))
    monkeypatch.setenv("HAL_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("HAL_ADMIN_TOKEN", "secret")

    with TestClient(create_app()) as client:
        rejected = client.post("/admin/restart", json={"reason": "test"})
        accepted = client.post(
            "/admin/restart",
            headers={"X-Hal-Admin-Token": "secret"},
            json={"reason": "test"},
        )

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "queued"
    assert restart_signal.exists()


def test_blooio_webhook_requires_valid_signature_when_secret_is_set(tmp_path, monkeypatch):
    db_path = tmp_path / "hal.sqlite3"
    secret = "whsec_test_secret"
    body = json.dumps({"chat_id": "+15551234567", "text": "signed hello"}).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + body,
        sha256,
    ).hexdigest()

    monkeypatch.setenv("HAL_DB_PATH", str(db_path))
    monkeypatch.setenv("HAL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HAL_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("BLOOIO_WEBHOOK_SECRET", secret)
    monkeypatch.delenv("BLOOIO_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HAL_WEBHOOK_TOKEN", raising=False)
    monkeypatch.delenv("HAL_ADMIN_TOKEN", raising=False)

    with TestClient(create_app()) as client:
        rejected = client.post(
            "/webhooks/blooio",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        accepted = client.post(
            "/webhooks/blooio",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Blooio-Signature": f"t={timestamp},v1={signature}",
            },
        )

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["reply"] == "I received: signed hello"
    webhooks = Database(db_path).list_webhooks(limit=10)
    assert len(webhooks) == 1
    assert webhooks[0]["text"] == "signed hello"
    assert webhooks[0]["status"] == "processed"
