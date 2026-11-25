from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class BatchPauseController:
    """Global pause gate for workers (401 renew / network outage)."""

    def __init__(self) -> None:
        self._resume = threading.Event()
        self._resume.set()
        self._lock = threading.Lock()
        self._reason: Optional[str] = None

    @property
    def is_paused(self) -> bool:
        return not self._resume.is_set()

    @property
    def reason(self) -> Optional[str]:
        with self._lock:
            return self._reason

    def pause(self, reason: str) -> None:
        with self._lock:
            self._reason = reason
            was_running = self._resume.is_set()
            self._resume.clear()
        if was_running:
            logger.warning("Batch paused: %s", reason)

    def resume(self) -> None:
        with self._lock:
            self._reason = None
            self._resume.set()
        logger.info("Batch resumed")

    def wait_if_paused(self, timeout: Optional[float] = None) -> bool:
        """Block while paused. Returns True if running (or became running)."""
        return self._resume.wait(timeout=timeout)


batch_pause_controller = BatchPauseController()
