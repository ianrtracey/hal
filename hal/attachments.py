"""Host outbound attachment files at a public URL so Blooio can fetch them.

Blooio's API only accepts attachment URLs (no direct upload), so we copy
local files into a public directory served by the FastAPI app and return
the resulting URL. The random ID in the filename acts as the capability
token — anyone with the URL can fetch the file.
"""
from __future__ import annotations

import re
import secrets
import shutil
from pathlib import Path

from .config import Settings


_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def host_attachment(settings: Settings, local_path: Path | str) -> str:
    """Copy a local file into the public attachments dir and return its URL."""
    src = Path(local_path)
    if not src.is_file():
        raise FileNotFoundError(f"{src} does not exist or is not a file")

    settings.attachments_dir.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_hex(8)}{src.suffix}"
    shutil.copy2(src, settings.attachments_dir / name)
    return f"{settings.public_base_url}/attachments/{name}"


def resolve_attachment(settings: Settings, name: str) -> Path | None:
    """Resolve a /attachments/{name} request to a file path, or None if invalid.

    Returns None for path-traversal attempts, missing files, or names that
    don't match the strict whitelist of safe characters.
    """
    if not _NAME_RE.fullmatch(name):
        return None
    base = settings.attachments_dir.resolve()
    path = (settings.attachments_dir / name).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None
    return path if path.is_file() else None
