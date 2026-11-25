from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from app.config import Settings, get_settings
from app.services.batch_job_tracker import batch_job_tracker
from app.services.rabbitmq.connection import get_queue_message_count
from app.services.token_renew_service import token_renew_service

logger = logging.getLogger(__name__)


class TokenRenewScheduler:
    """
    Every TOKEN_RENEW_INTERVAL_SECONDS (default 50 min), renew token
    ONLY when RabbitMQ queue or active batch job items are non-zero.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.settings.batch_use_rabbitmq:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="ocr-token-renew-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Token renew scheduler started (interval=%ss)",
            self.settings.token_renew_interval_seconds,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _has_running_work(self) -> bool:
        pending_jobs = batch_job_tracker.active_item_count()
        queue_depth = get_queue_message_count(self.settings)
        has_work = pending_jobs > 0 or queue_depth > 0
        logger.info(
            "Token renew gate: pending_job_items=%s queue_depth=%s => %s",
            pending_jobs,
            queue_depth,
            "renew" if has_work else "skip",
        )
        return has_work

    def _loop(self) -> None:
        interval = max(60, int(self.settings.token_renew_interval_seconds))
        # Wait full interval before first scheduled renew (401 path renews immediately)
        while not self._stop.wait(timeout=interval):
            try:
                if not self._has_running_work():
                    logger.info(
                        "Skipping scheduled token renew — no running queue items"
                    )
                    continue
                token_renew_service.pause_renew_resume(reason="scheduled_50m")
            except Exception:  # noqa: BLE001
                logger.exception("Scheduled token renew failed")


token_renew_scheduler = TokenRenewScheduler()
