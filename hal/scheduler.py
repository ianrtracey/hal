from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler

from .config import Settings
from .db import Database


def start_scheduler(settings: Settings, db: Database) -> BackgroundScheduler | None:
    if not settings.scheduler_enabled:
        return None

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        lambda: db.record_task("scheduler.heartbeat", "ok", {"source": "apscheduler"}),
        trigger="interval",
        minutes=60,
        id="scheduler.heartbeat",
        replace_existing=True,
    )
    scheduler.start()
    return scheduler

