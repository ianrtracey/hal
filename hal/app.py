from __future__ import annotations

import asyncio
import json
import traceback
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Request

from .blooio_signature import BlooioSignatureError, verify_blooio_signature
from .config import Settings, get_settings
from .db import Database
from .llm import LLMClient
from .restart import request_restart
from .scheduler import start_scheduler
from .self_modify import GuardrailViolation, run_validation, validate_edit_paths
from .service import HalService, parse_blooio_payload


def _check_token(expected: str | None, provided: str | None, label: str) -> None:
    if expected and provided != expected:
        raise HTTPException(status_code=401, detail=f"Invalid {label} token")


def _check_required_token(expected: str | None, provided: str | None, label: str) -> None:
    if not expected:
        raise HTTPException(status_code=403, detail=f"{label} token is not configured")
    _check_token(expected, provided, label)


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted = {"authorization", "cookie", "x-blooio-signature", "x-hal-webhook-token"}
    return {
        key: "[redacted]" if key.lower() in redacted else value
        for key, value in headers.items()
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    db = Database(settings.db_path)
    llm = LLMClient(settings)
    scheduler = start_scheduler(settings, db)

    app.state.settings = settings
    app.state.db = db
    app.state.service = HalService(settings, db, llm)
    app.state.scheduler = scheduler

    try:
        yield
    finally:
        if scheduler:
            scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(title="Hal", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        request.app.state.db.health_check()
        scheduler = request.app.state.scheduler
        return {
            "status": "ok",
            "database": "ok",
            "scheduler": "running" if scheduler and scheduler.running else "disabled",
        }

    @app.post("/webhooks/blooio")
    async def blooio_webhook(
        request: Request,
        x_hal_webhook_token: str | None = Header(default=None),
        x_blooio_signature: str | None = Header(default=None),
        x_blooio_event: str | None = Header(default=None),
        x_blooio_message_id: str | None = Header(default=None),
    ) -> dict[str, Any]:
        settings = request.app.state.settings
        db = request.app.state.db
        raw_body = await request.body()
        if settings.blooio_webhook_secret:
            try:
                verify_blooio_signature(
                    raw_body,
                    x_blooio_signature,
                    settings.blooio_webhook_secret,
                    settings.webhook_signature_tolerance_seconds,
                )
            except BlooioSignatureError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
        else:
            token = x_hal_webhook_token or request.query_params.get("token")
            _check_token(settings.webhook_token, token, "webhook")

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Webhook JSON body must be an object")

        event_type = x_blooio_event
        if not event_type and isinstance(payload.get("event"), str):
            event_type = payload["event"]

        event_id = x_blooio_message_id
        chat_id = None
        text = None
        try:
            inbound = parse_blooio_payload(payload)
            event_id = event_id or inbound.message_id
            chat_id = inbound.chat_id
            text = inbound.text
        except ValueError:
            pass

        existing = db.find_webhook_by_event("blooio", event_id) if event_id else None
        if existing:
            duplicate_id = db.record_webhook(
                provider="blooio",
                event_type=event_type,
                event_id=event_id,
                chat_id=chat_id,
                text=text,
                status="duplicate",
                headers=_safe_headers(dict(request.headers)),
                payload=payload,
            )
            return {
                "status": "duplicate",
                "event_type": event_type,
                "webhook_id": duplicate_id,
                "original_webhook_id": existing["id"],
            }

        webhook_id = db.record_webhook(
            provider="blooio",
            event_type=event_type,
            event_id=event_id,
            chat_id=chat_id,
            text=text,
            status="received",
            headers=_safe_headers(dict(request.headers)),
            payload=payload,
        )
        if event_type and event_type != "message.received":
            db.update_webhook_status(webhook_id, "ignored")
            return {"status": "ignored", "event_type": event_type, "webhook_id": webhook_id}

        try:
            result = await request.app.state.service.handle_inbound_sms(payload)
            db.update_webhook_status(webhook_id, "processed")
            result["webhook_id"] = webhook_id
            return result
        except ValueError as exc:
            db.update_webhook_status(webhook_id, "invalid", str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            db.update_webhook_status(webhook_id, "error", str(exc))
            db.record_error(
                source="webhook.blooio",
                severity="error",
                message=str(exc),
                traceback_text=traceback.format_exc(),
                raw=payload,
            )
            raise

    @app.post("/admin/restart")
    async def restart(
        request: Request,
        payload: dict[str, Any] | None = Body(default=None),
        x_hal_admin_token: str | None = Header(default=None),
    ) -> dict[str, str]:
        token = x_hal_admin_token or request.query_params.get("token")
        _check_required_token(request.app.state.settings.admin_token, token, "admin")
        reason = str((payload or {}).get("reason") or "admin endpoint")
        path = request_restart(request.app.state.settings, reason)
        return {"status": "queued", "path": str(path)}

    @app.post("/admin/validate-edit")
    async def validate_edit(
        request: Request,
        payload: dict[str, Any] = Body(...),
        x_hal_admin_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        token = x_hal_admin_token or request.query_params.get("token")
        _check_required_token(request.app.state.settings.admin_token, token, "admin")
        paths = payload.get("paths")
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            raise HTTPException(status_code=400, detail="paths must be a list of strings")
        try:
            validate_edit_paths(request.app.state.settings, paths)
        except GuardrailViolation as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result = await asyncio.to_thread(run_validation, request.app.state.settings)
        return {
            "ok": result.ok,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    @app.post("/admin/groups")
    async def create_group(
        request: Request,
        payload: dict[str, Any] = Body(...),
        x_hal_admin_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Create a Blooio group and optionally send an initial message to link the chat."""
        token = x_hal_admin_token or request.query_params.get("token")
        _check_required_token(request.app.state.settings.admin_token, token, "admin")
        settings = request.app.state.settings
        if not settings.blooio_api_key:
            raise HTTPException(status_code=500, detail="BLOOIO_API_KEY not configured")

        from blooio_client import BlooioClient

        client = BlooioClient(api_key=settings.blooio_api_key)
        name = payload.get("name", "Hal Group")
        members = payload.get("members", [])
        initial_message = payload.get("message")

        group = client.create_group(name, members=members or None)
        result: dict[str, Any] = {"group": group}

        if initial_message and group.get("group_id"):
            msg = client.send_message(group["group_id"], initial_message)
            result["initial_message"] = msg

        return result

    @app.get("/admin/groups")
    async def list_groups(
        request: Request,
        x_hal_admin_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        token = x_hal_admin_token or request.query_params.get("token")
        _check_required_token(request.app.state.settings.admin_token, token, "admin")
        settings = request.app.state.settings
        if not settings.blooio_api_key:
            raise HTTPException(status_code=500, detail="BLOOIO_API_KEY not configured")

        from blooio_client import BlooioClient

        client = BlooioClient(api_key=settings.blooio_api_key)
        return client.list_groups()

    return app


app = create_app()
