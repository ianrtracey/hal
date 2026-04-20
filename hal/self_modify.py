from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import Settings


PROTECTED_PATHS = {
    ".env",
    ".git",
    ".venv",
    "supervisor.py",
    "var",
}


class GuardrailViolation(ValueError):
    pass


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str


def validate_edit_paths(settings: Settings, paths: list[str]) -> None:
    repo_root = settings.repo_root.resolve()
    for raw_path in paths:
        candidate = (repo_root / raw_path).resolve()
        try:
            relative = candidate.relative_to(repo_root)
        except ValueError as exc:
            raise GuardrailViolation(f"Path escapes repo: {raw_path}") from exc

        first_part = relative.parts[0] if relative.parts else ""
        if str(relative) in PROTECTED_PATHS or first_part in PROTECTED_PATHS:
            raise GuardrailViolation(f"Path is protected: {raw_path}")


def run_validation(settings: Settings) -> ValidationResult:
    command = [
        sys.executable,
        "-m",
        "compileall",
        "-q",
        "hal",
        "blooio_client.py",
    ]
    result = subprocess.run(
        command,
        cwd=settings.repo_root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return ValidationResult(
        ok=result.returncode == 0,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )

