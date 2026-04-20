from __future__ import annotations

import hmac
import time
from hashlib import sha256


class BlooioSignatureError(ValueError):
    pass


def verify_blooio_signature(
    raw_body: bytes,
    signature_header: str | None,
    secret: str,
    tolerance_seconds: int = 300,
) -> None:
    if not signature_header:
        raise BlooioSignatureError("Missing Blooio signature")

    parts: dict[str, str] = {}
    for part in signature_header.split(","):
        key, separator, value = part.partition("=")
        if separator:
            parts[key.strip()] = value.strip()

    timestamp = parts.get("t")
    signature = parts.get("v1")
    if not timestamp or not signature:
        raise BlooioSignatureError("Invalid Blooio signature format")

    try:
        timestamp_int = int(timestamp)
    except ValueError as exc:
        raise BlooioSignatureError("Invalid Blooio signature timestamp") from exc

    age = int(time.time()) - timestamp_int
    if age > tolerance_seconds:
        raise BlooioSignatureError("Blooio signature timestamp too old")
    if age < -tolerance_seconds:
        raise BlooioSignatureError("Blooio signature timestamp too far in the future")

    signed_payload = timestamp.encode("utf-8") + b"." + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed_payload, sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise BlooioSignatureError("Blooio signature mismatch")

