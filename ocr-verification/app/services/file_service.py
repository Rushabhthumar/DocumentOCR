from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.config import Settings, get_settings
from app.models.document_models import DocumentBinary
from app.utils.file_utils import cleanup_path, write_temp_file

logger = logging.getLogger(__name__)


class FileService:
    """Downloads supporting documents via GET /File/DownloadOCR/{file_id} only."""

    def __init__(self, settings: Optional[Settings] = None, client: Optional[httpx.Client] = None):
        self.settings = settings or get_settings()
        self._client = client

    def _download_header_variants(self) -> list[dict[str, str]]:
        """
        Mirror Angular Files download headers.
        Try with Apikey first (browser network tab), then without (worked earlier).
        """
        base = self.settings.auth_headers(for_files=True)
        token = (base.get(self.settings.api_auth_header) or "").strip()
        app_id = base.get("Appid") or base.get("AppId") or ""
        api_key = base.get("Apikey") or ""

        variants: list[dict[str, str]] = []
        # Exact Angular-style names from DevTools
        primary = {
            "Authorization": token,
            "Accept": "*/*",
            "Response-Type": "blob",
        }
        if app_id:
            primary["Appid"] = app_id
        if api_key:
            primary["Apikey"] = api_key
            variants.append(dict(primary))
            # Retry without Apikey if server rejects scheme with it
            no_key = dict(primary)
            no_key.pop("Apikey", None)
            variants.append(no_key)
        else:
            variants.append(primary)
        return variants

    def _get_client(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(timeout=120.0)

    def download_file(self, file_id: str | int, filename: Optional[str] = None) -> DocumentBinary:
        # Try both paths — prod often allows /File/Download but not DownloadOCR (and vice versa)
        candidates: list[str] = []
        for path in (
            self.settings.file_download_path.format(file_id=file_id),
            f"/File/Download/{file_id}",
            f"/File/DownloadOCR/{file_id}",
        ):
            if path not in candidates:
                candidates.append(path)

        owns_client = self._client is None
        client = self._get_client()
        try:
            header_variants = self._download_header_variants()
            last_error: Exception | None = None
            response = None
            for headers in header_variants:
                auth_val = headers.get("Authorization") or ""
                logger.info(
                    "File download file_id=%s auth=%s bearer=%s appId=%s apikey=%s",
                    file_id,
                    bool(auth_val),
                    auth_val.lower().startswith("bearer "),
                    bool(headers.get("Appid")),
                    bool(headers.get("Apikey")),
                )
                for path in candidates:
                    url = f"{self.settings.file_api_base_url.rstrip('/')}{path}"
                    response = client.get(url, headers=headers)
                    logger.info("File GET %s -> %s", url, response.status_code)
                    if response.status_code in {401, 403}:
                        body_preview = (response.text or "")[:240].replace("\n", " ")
                        logger.warning(
                            "File %s on %s body=%s",
                            response.status_code,
                            path,
                            body_preview,
                        )
                        last_error = httpx.HTTPStatusError(
                            f"{response.status_code} for {url}",
                            request=response.request,
                            response=response,
                        )
                        continue
                    if response.status_code == 404:
                        last_error = FileNotFoundError(f"File not found for file_id={file_id}")
                        continue
                    try:
                        response.raise_for_status()
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                        continue
                    break
                else:
                    continue
                break
            else:
                if isinstance(last_error, FileNotFoundError):
                    raise last_error
                raise last_error or RuntimeError(f"Failed to download file_id={file_id}")

            content = response.content
            if not content:
                raise ValueError(f"Empty file content for file_id={file_id}")

            content_type = response.headers.get("content-type")
            content_disposition = response.headers.get("content-disposition", "")
            if not filename and "filename=" in content_disposition:
                filename = content_disposition.split("filename=")[-1].strip().strip('"')

            local_path = write_temp_file(
                self.settings,
                content,
                filename=filename,
                content_type=content_type,
            )
            return DocumentBinary(
                file_id=str(file_id),
                filename=filename or local_path.name,
                content_type=content_type,
                size_bytes=len(content),
                local_path=str(local_path),
            )
        finally:
            if owns_client:
                client.close()

    def cleanup(self, document: Optional[DocumentBinary]) -> None:
        if document:
            cleanup_path(document.local_path)