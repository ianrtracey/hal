from __future__ import annotations

import json
from pathlib import Path

from .config import Settings
from .db import utc_now


def request_restart(settings: Settings, reason: str) -> Path:
    settings.restart_signal_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"requested_at": utc_now(), "reason": reason}
    settings.restart_signal_path.write_text(json.dumps(payload, indent=2) + "\n")
    return settings.restart_signal_path

