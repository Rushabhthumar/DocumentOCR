from __future__ import annotations

import errno
from typing import Any


class TransientNetworkError(Exception):
    """DNS / connect failures that should be retried, not EXTRACTION_FAILED."""


class AuthExpiredError(Exception):
    """HTTP 401/403 — pause batch and renew token before retrying."""


def is_transient_network_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    needles = (
        "getaddrinfo failed",
        "name or service not known",
        "nodename nor servname",
        "temporary failure in name resolution",
        "connecterror",
        "connecttimeout",
        "readtimeout",
        "timed out",
        "connection reset",
        "connection refused",
        "network is unreachable",
        "failed to resolve",
        "errno 11001",
        "errno 11002",
        "eai_noname",
    )
    eai = getattr(errno, "EAI_NONAME", None)
    if eai is not None:
        needles = (*needles, str(eai).lower())
    if any(n in text for n in needles):
        return True
    cause = getattr(exc, "__cause__", None)
    if cause is not None and cause is not exc:
        return is_transient_network_error(cause)
    return False


def is_auth_error(exc: BaseException) -> bool:
    text = f"{exc}".lower()
    if "401" in text or "403" in text or "unauthorized" in text:
        return True
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in {401, 403}


def dig_json_path(data: Any, path: str) -> Any:
    cur: Any = data
    for part in (path or "").split("."):
        if not part:
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur
