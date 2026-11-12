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

    def _get_client(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(timeout=120.0, headers=self.settings.auth_headers())

    def download_file(self, file_id: str | int, filename: Optional[str] = None) -> DocumentBinary:
        path = self.settings.file_download_path.format(file_id=file_id)
        url = f"{self.settings.file_api_base_url.rstrip('/')}{path}"
        owns_client = self._client is None
        client = self._get_client()
        try:
            response = client.get(url)
            if response.status_code == 404:
                raise FileNotFoundError(f"File not found for file_id={file_id}")
            response.raise_for_status()

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