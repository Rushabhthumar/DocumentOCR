from __future__ import annotations

import logging
import threading
from typing import Optional

import httpx

from app.config import Settings, get_settings
from app.services.auth_token_store import auth_token_store
from app.services.batch_pause_controller import batch_pause_controller
from app.services.network_errors import dig_json_path

logger = logging.getLogger(__name__)


class TokenRenewService:
    """
    Calls renew-token API when configured.
    TOKEN_RENEW_API_URL is empty until you share the endpoint — renew is then a no-op
    besides logging (caller still pauses/resumes around this).
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._lock = threading.Lock()

    def renew(self, *, reason: str = "manual") -> bool:
        """
        Fetch a new token and store it. Returns True if token was updated.
        Safe to call concurrently — only one renew runs at a time.
        """
        with self._lock:
            url = (self.settings.token_renew_api_url or "").strip()
            if not url:
                logger.warning(
                    "Token renew skipped (%s): TOKEN_RENEW_API_URL not set yet",
                    reason,
                )
                return False

            method = (self.settings.token_renew_api_method or "POST").upper()
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
            # Prefer current token when renew endpoint needs it
            current = auth_token_store.get_authorization()
            if current:
                headers["Authorization"] = current
            app_id = auth_token_store.get_app_id() or self.settings.file_app_id
            if app_id:
                headers["AppId"] = app_id
                headers["Appid"] = app_id

            logger.info("Renewing auth token via %s %s (reason=%s)", method, url, reason)
            with httpx.Client(timeout=60.0) as client:
                if method == "GET":
                    response = client.get(url, headers=headers)
                else:
                    response = client.post(url, headers=headers, json={})
                response.raise_for_status()
                data = response.json() if response.content else {}

            token = dig_json_path(data, self.settings.token_renew_response_token_path)
            if not token and isinstance(data, dict):
                for key in ("accessToken", "access_token", "token", "Token"):
                    if data.get(key):
                        token = data[key]
                        break
                    nested = data.get("data")
                    if isinstance(nested, dict) and nested.get(key):
                        token = nested[key]
                        break

            if not token or not str(token).strip():
                raise RuntimeError(
                    f"Token renew response missing token at "
                    f"{self.settings.token_renew_response_token_path}: {str(data)[:240]}"
                )

            auth_token_store.set_authorization(str(token))
            logger.info("Auth token renewed successfully (reason=%s)", reason)
            return True

    def pause_renew_resume(self, *, reason: str) -> bool:
        batch_pause_controller.pause(reason)
        try:
            ok = self.renew(reason=reason)
            return ok
        finally:
            batch_pause_controller.resume()


token_renew_service = TokenRenewService()
