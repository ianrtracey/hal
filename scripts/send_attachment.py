#!/usr/bin/env python3
"""Send a message with an attachment via Blooio.

Hosts the local file at a public URL (via Hal's /attachments/{id} route)
so Blooio can fetch it server-side, then sends a message referencing it.

Defaults are wired up to send /data/bot.mp3 to the
NYC summer 2026 group chat (Ian, Mason, Isaac, Hal).
Override with --to and a positional path to send anything else.

Usage:
    uv run python scripts/send_attachment.py [<local_path>] [--to PHONE] [--text "caption"]

Examples:
    # Send the default Hal voice note to the NYC group
    uv run python scripts/send_attachment.py

    # Custom file and recipient
    uv run python scripts/send_attachment.py /data/sample.m4a \\
        --to +15551234567 --text "have a listen"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blooio_client import BlooioClient  # noqa: E402
from hal.attachments import host_attachment  # noqa: E402
from hal.config import get_settings  # noqa: E402


# NYC summer 2026 crew: Ian, Mason, Isaac, Hal
DEFAULT_RECIPIENT = "grp_0693bd3478ac4463"
DEFAULT_LOCAL_PATH = "/data/bot.mp3"


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a message with an attachment via Blooio.")
    parser.add_argument(
        "local_path",
        nargs="?",
        default=DEFAULT_LOCAL_PATH,
        help=f"Path to a local file to attach (default: {DEFAULT_LOCAL_PATH}).",
    )
    parser.add_argument(
        "--to",
        default=DEFAULT_RECIPIENT,
        help=f"Recipient phone number, email, or group ID (default: {DEFAULT_RECIPIENT})",
    )
    parser.add_argument(
        "--text",
        default=None,
        help="Optional caption text to send alongside the attachment.",
    )
    args = parser.parse_args()

    settings = get_settings()
    try:
        url = host_attachment(settings, args.local_path)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Hosted at: {url}", file=sys.stderr)

    client = BlooioClient()
    try:
        resp = client.send_message(args.to, args.text, attachments=[url])
    except Exception as exc:
        print(f"Send failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(resp, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
