from __future__ import annotations

import argparse
import json
import sys
import traceback
from typing import Any

from blooio_client import BlooioClient

from .config import Settings, get_settings
from .db import Database
from .llm import LLMClient
from .service import HalService


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True))


def _send_sms(settings: Settings, db: Database, chat_id: str, text: str) -> int:
    sent = False
    blooio_response = None
    send_error = None
    if settings.blooio_api_key:
        try:
            blooio_response = BlooioClient(api_key=settings.blooio_api_key).send_message(chat_id, text)
            sent = True
        except Exception as exc:
            send_error = str(exc)
            db.record_error(
                source="hal.cli.send-sms",
                severity="error",
                message=send_error,
                traceback_text=traceback.format_exc(),
                raw={"chat_id": chat_id, "text": text},
            )

    message_id = db.record_message(
        chat_id,
        "outbound",
        text,
        {"blooio_response": blooio_response, "sent": sent, "send_error": send_error, "source": "hal.cli"},
    )
    result: dict[str, Any] = {"ok": True, "chat_id": chat_id, "message_id": message_id, "sent": sent}
    if send_error:
        result["send_error"] = send_error
    _print_json(result)
    return 0


def _thinking(settings: Settings, db: Database, chat_id: str, state: str) -> int:
    sent = False
    blooio_response = None
    if settings.blooio_api_key:
        try:
            client = BlooioClient(api_key=settings.blooio_api_key)
            if state == "on":
                blooio_response = client.start_typing(chat_id)
            else:
                blooio_response = client.stop_typing(chat_id)
            sent = True
        except Exception as exc:
            db.record_error(
                source="hal.cli.thinking",
                severity="error",
                message=str(exc),
                traceback_text=traceback.format_exc(),
                raw={"chat_id": chat_id, "state": state},
            )
            _print_json(
                {"ok": False, "chat_id": chat_id, "state": state, "sent": False, "error": str(exc)}
            )
            return 1

    db.record_message(
        chat_id,
        "system",
        f"thinking:{state}",
        {"blooio_response": blooio_response, "sent": sent, "source": "hal.cli"},
    )
    _print_json({"ok": True, "chat_id": chat_id, "state": state, "sent": sent})
    return 0


def _note(db: Database, chat_id: str, text: str) -> int:
    message_id = db.record_message(chat_id, "system", text, {"source": "hal.cli.note"})
    _print_json({"ok": True, "chat_id": chat_id, "message_id": message_id})
    return 0


def _simulate_inbound(settings: Settings, db: Database, chat_id: str, text: str) -> int:
    import asyncio

    service = HalService(settings, db, LLMClient(settings))
    result = asyncio.run(
        service.handle_inbound_sms({"chat_id": chat_id, "text": text, "source": "simulate-inbound"})
    )
    _print_json({"ok": True, **result})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="halctl")
    subparsers = parser.add_subparsers(dest="command", required=True)

    send_sms = subparsers.add_parser("send-sms", help="send a user-visible SMS")
    send_sms.add_argument("--chat-id", required=True)
    send_sms.add_argument("--text", required=True)

    thinking = subparsers.add_parser("thinking", help="set visible thinking/typing state")
    thinking.add_argument("--chat-id", required=True)
    thinking.add_argument("--state", choices=("on", "off"), required=True)

    note = subparsers.add_parser("note", help="record an internal harness note")
    note.add_argument("--chat-id", required=True)
    note.add_argument("--text", required=True)

    simulate = subparsers.add_parser("simulate-inbound", help="simulate an inbound SMS")
    simulate.add_argument("--chat-id", required=True)
    simulate.add_argument("--text", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    db = Database(settings.db_path)

    if args.command == "send-sms":
        return _send_sms(settings, db, args.chat_id, args.text)
    if args.command == "thinking":
        return _thinking(settings, db, args.chat_id, args.state)
    if args.command == "note":
        return _note(db, args.chat_id, args.text)
    if args.command == "simulate-inbound":
        return _simulate_inbound(settings, db, args.chat_id, args.text)

    _print_json({"ok": False, "error": f"unknown command: {args.command}"})
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
