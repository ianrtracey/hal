from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    repo_root: Path = REPO_ROOT
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("HAL_DATA_DIR", REPO_ROOT / "var"))
    )
    db_path: Path = field(
        default_factory=lambda: Path(os.environ.get("HAL_DB_PATH", REPO_ROOT / "var" / "hal.sqlite3"))
    )
    restart_signal_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("HAL_RESTART_SIGNAL_PATH", REPO_ROOT / "var" / "restart.request")
        )
    )

    blooio_api_key: str | None = field(default_factory=lambda: os.environ.get("BLOOIO_API_KEY"))
    anthropic_api_key: str | None = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    )
    agent_enabled: bool = field(default_factory=lambda: _env_bool("HAL_AGENT_ENABLED", False))
    claude_command: list[str] = field(
        default_factory=lambda: shlex.split(os.environ.get("HAL_CLAUDE_COMMAND", "claude"))
    )
    claude_timeout_seconds: int = field(
        default_factory=lambda: int(os.environ.get("HAL_CLAUDE_TIMEOUT_SECONDS", "120"))
    )
    prompt_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("HAL_PROMPT_DIR", REPO_ROOT / "prompts"))
    )

    webhook_token: str | None = field(default_factory=lambda: os.environ.get("HAL_WEBHOOK_TOKEN"))
    admin_token: str | None = field(default_factory=lambda: os.environ.get("HAL_ADMIN_TOKEN"))
    blooio_webhook_secret: str | None = field(
        default_factory=lambda: os.environ.get("BLOOIO_WEBHOOK_SECRET")
    )
    webhook_signature_tolerance_seconds: int = field(
        default_factory=lambda: int(os.environ.get("HAL_WEBHOOK_SIGNATURE_TOLERANCE_SECONDS", "300"))
    )

    scheduler_enabled: bool = field(
        default_factory=lambda: _env_bool("HAL_SCHEDULER_ENABLED", True)
    )
    max_context_messages: int = field(
        default_factory=lambda: int(os.environ.get("HAL_MAX_CONTEXT_MESSAGES", "20"))
    )


def get_settings() -> Settings:
    return Settings()
