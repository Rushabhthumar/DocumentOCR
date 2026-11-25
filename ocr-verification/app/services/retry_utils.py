from __future__ import annotations

import logging
import random
import time
from typing import Callable, Optional, TypeVar

from app.config import Settings, get_settings
from app.services.batch_pause_controller import batch_pause_controller
from app.services.network_errors import (
    AuthExpiredError,
    TransientNetworkError,
    is_auth_error,
    is_transient_network_error,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")


def retry_with_backoff(
    fn: Callable[[], T],
    *,
    settings: Optional[Settings] = None,
    label: str = "operation",
    on_auth_expired: Optional[Callable[[], None]] = None,
) -> T:
    """
    Retry transient DNS/connect errors with exponential backoff.
    On auth errors, optionally renew then retry once more after pause/resume.
    """
    cfg = settings or get_settings()
    max_attempts = max(1, int(cfg.network_retry_max_attempts))
    base = float(cfg.network_retry_base_delay_seconds)
    max_delay = float(cfg.network_retry_max_delay_seconds)
    last_exc: BaseException | None = None
    auth_retried = False

    for attempt in range(1, max_attempts + 1):
        batch_pause_controller.wait_if_paused()
        try:
            return fn()
        except AuthExpiredError as exc:
            last_exc = exc
            if auth_retried or on_auth_expired is None:
                raise
            logger.warning("%s auth expired — renewing token then retrying", label)
            on_auth_expired()
            auth_retried = True
            continue
        except Exception as exc:  # noqa: BLE001
            if is_auth_error(exc):
                last_exc = AuthExpiredError(str(exc))
                if auth_retried or on_auth_expired is None:
                    raise AuthExpiredError(str(exc)) from exc
                logger.warning("%s got 401/403 — renewing token then retrying", label)
                on_auth_expired()
                auth_retried = True
                continue
            if not is_transient_network_error(exc):
                raise
            last_exc = exc
            if attempt >= max_attempts:
                break
            delay = min(max_delay, base * (2 ** (attempt - 1)))
            delay = delay * (0.8 + 0.4 * random.random())
            logger.warning(
                "%s transient network error attempt %s/%s: %s — sleep %.1fs",
                label,
                attempt,
                max_attempts,
                exc,
                delay,
            )
            # Pause workers briefly so we don't stampede DNS while down
            if attempt >= 2:
                batch_pause_controller.pause(f"network:{exc}")
                try:
                    time.sleep(delay)
                finally:
                    batch_pause_controller.resume()
            else:
                time.sleep(delay)

    raise TransientNetworkError(
        f"{label} failed after {max_attempts} attempts: {last_exc}"
    ) from last_exc
