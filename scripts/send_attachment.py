#!/usr/bin/env python3
"""Send a message with an attachment via Blooio.

Hosts the local file at a public URL (via Hal's /attachments/{id} route)
so Blooio can fetch it server-side, then sends a message referencing it.

Usage:
    uv run python scripts/send_attachment.py <local_path> [--to PHONE] [--text "caption"]

Examples:
    # Send /data/sample.m4a to Ian's number with no caption
    uv run python scripts/send_attachment.py /data/sample.m4a

    # Custom recipient and caption
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


DEFAULT_RECIPIENT = "+16238662766"


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a message with an attachment via Blooio.")
    parser.add_argument("local_path", help="Path to a local file to attach.")
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
