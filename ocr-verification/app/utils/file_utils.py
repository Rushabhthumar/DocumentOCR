from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from app.config import Settings

logger = logging.getLogger(__name__)


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


def save_failed_verification_documents(
    settings: Settings,
    *,
    transaction_id: str,
    decision: str,
    message: str = "",
    errors: Optional[list[str]] = None,
    documents: list[Any],
) -> Path | None:
    """
    Copy downloaded supporting files for a failed verification into
    FailedVarificationDocuments/{transaction_id}/ for later review/training.
    """
    if not documents:
        return None

    txn_dir = settings.failed_verification_path / sanitize_filename(transaction_id)
    txn_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, str]] = []

    for doc in documents:
        local_path = getattr(doc, "local_path", None)
        if not local_path:
            continue
        src = Path(str(local_path))
        if not src.is_file():
            logger.warning("Failed-doc source missing: %s", src)
            continue
        file_id = str(getattr(doc, "file_id", "") or "unknown")
        original_name = getattr(doc, "filename", None) or src.name
        safe_name = sanitize_filename(original_name)
        dest_name = f"{sanitize_filename(file_id)}_{safe_name}"
        dest = txn_dir / dest_name
        try:
            shutil.copy2(src, dest)
            saved.append(
                {
                    "file_id": file_id,
                    "filename": original_name,
                    "saved_as": dest_name,
                }
            )
        except OSError as exc:
            logger.warning("Could not save failed doc %s: %s", src, exc)

    if not saved:
        return None

    meta = {
        "transaction_id": transaction_id,
        "decision": decision,
        "message": message,
        "errors": errors or [],
        "documents": saved,
    }
    meta_path = txn_dir / "_failure_meta.json"
    try:
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write failure meta for %s: %s", transaction_id, exc)

    logger.info(
        "Saved %s failed verification file(s) for txn=%s under %s",
        len(saved),
        transaction_id,
        txn_dir,
    )
    return txn_dir