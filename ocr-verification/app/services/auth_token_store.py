from __future__ import annotations

import threading
from typing import Optional


class AuthTokenStore:
    """
    Process-wide auth used by RabbitMQ workers (outlives HTTP request ContextVar).
    Updated from middleware /batch headers and from token renew.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._authorization: Optional[str] = None
        self._app_id: Optional[str] = None
        self._api_key: Optional[str] = None

    def update(
        self,
        authorization: Optional[str] = None,
        app_id: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        with self._lock:
            if authorization is not None:
                token = authorization.strip()
                if token and not token.lower().startswith("bearer "):
                    token = f"Bearer {token}"
                self._authorization = token or None
            if app_id is not None:
                self._app_id = app_id.strip() or None
            if api_key is not None:
                self._api_key = api_key.strip() or None

    def set_authorization(self, authorization: Optional[str]) -> None:
        self.update(authorization=authorization)

    def get_authorization(self) -> Optional[str]:
        with self._lock:
            return self._authorization

    def get_app_id(self) -> Optional[str]:
        with self._lock:
            return self._app_id

    def get_api_key(self) -> Optional[str]:
        with self._lock:
            return self._api_key

    def snapshot(self) -> dict[str, Optional[str]]:
        with self._lock:
            return {
                "authorization": self._authorization,
                "app_id": self._app_id,
                "api_key": self._api_key,
            }


auth_token_store = AuthTokenStore()
