from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

# Request-scoped auth forwarded from Angular (Bearer token + AppId).
_authorization: ContextVar[Optional[str]] = ContextVar("authorization", default=None)
_app_id: ContextVar[Optional[str]] = ContextVar("app_id", default=None)


def set_request_auth(authorization: Optional[str], app_id: Optional[str] = None) -> None:
    _authorization.set(authorization)
    _app_id.set(app_id)


def clear_request_auth() -> None:
    _authorization.set(None)
    _app_id.set(None)


def get_request_authorization() -> Optional[str]:
    return _authorization.get()


def get_request_app_id() -> Optional[str]:
    return _app_id.get()
