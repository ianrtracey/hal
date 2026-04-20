from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def disable_agent_harness_by_default(monkeypatch):
    monkeypatch.setenv("HAL_AGENT_ENABLED", "0")
