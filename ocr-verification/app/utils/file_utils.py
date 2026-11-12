from __future__ import annotations

import hashlib
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Optional

from app.config import Settings


ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "application/octet-stream",
}


def sanitize_filename(name: Optional[str]) -> str:
    if not name:
        return f"file_{uuid.uuid4().hex}"
    cleaned = re.sub(r"[^\w.\-]+", "_", name).strip("._")
    return cleaned or f"file_{uuid.uuid4().hex}"


def detect_extension(filename: Optional[str], content_type: Optional[str], data: bytes) -> str:
    if filename:
        ext = Path(filename).suffix.lower()
        if ext in ALLOWED_EXTENSIONS:
            return ext
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ""
        if guessed == ".jpe":
            guessed = ".jpg"
        if guessed in ALLOWED_EXTENSIONS:
            return guessed
    if data[:4] == b"%PDF":
        return ".pdf"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return ".bin"


def validate_file_bytes(
    data: bytes,
    settings: Settings,
    filename: Optional[str] = None,
    content_type: Optional[str] = None,
) -> tuple[bool, str, str]:
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    if not data:
        return False, "", "Empty file content"
    if len(data) > max_bytes:
        return False, "", f"File exceeds max size of {settings.max_file_size_mb} MB"
    ext = detect_extension(filename, content_type, data)
    if ext not in ALLOWED_EXTENSIONS:
        return False, ext, f"Unsupported file type: {ext or 'unknown'}"
    if content_type and content_type.split(";")[0].strip().lower() not in ALLOWED_CONTENT_TYPES:
        # Still allow if magic bytes prove supported type
        if ext not in ALLOWED_EXTENSIONS:
            return False, ext, f"Unsupported content type: {content_type}"
    return True, ext, ""


def write_temp_file(settings: Settings, data: bytes, filename: Optional[str] = None, content_type: Optional[str] = None) -> Path:
    ok, ext, err = validate_file_bytes(data, settings, filename, content_type)
    if not ok:
        raise ValueError(err)
    digest = hashlib.sha256(data).hexdigest()[:12]
    safe_name = sanitize_filename(filename)
    stem = Path(safe_name).stem[:40]
    out = settings.temp_path / f"{stem}_{digest}_{uuid.uuid4().hex[:8]}{ext}"
    out.write_bytes(data)
    return out


def cleanup_path(path: Optional[str | Path]) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass