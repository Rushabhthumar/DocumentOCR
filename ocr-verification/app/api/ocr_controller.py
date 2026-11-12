from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config import get_settings
from app.services.extraction_service import ExtractionService
from app.utils.file_utils import cleanup_path, write_temp_file

router = APIRouter(prefix="/api/ocr", tags=["OCR"])


@router.post("/extract")
async def extract_uploaded_document(file: UploadFile = File(...)):
    """Extract structured data from an uploaded local document (for testing)."""
    settings = get_settings()
    data = await file.read()
    local_path = None
    try:
        local_path = write_temp_file(
            settings,
            data,
            filename=file.filename,
            content_type=file.content_type,
        )
        service = ExtractionService(settings)
        result = service.extract_file(
            local_path,
            file_id=f"upload:{file.filename}",
            filename=file.filename,
        )
        return result.model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Extraction failed: {exc}") from exc
    finally:
        cleanup_path(local_path)


@router.post("/extract-sample/{sample_name}")
def extract_sample_document(sample_name: str):
    """Extract from a file under ./samples for local demos."""
    samples_dir = Path(__file__).resolve().parents[2] / "samples"
    path = samples_dir / sample_name
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Sample not found: {sample_name}")
    service = ExtractionService(get_settings())
    result = service.extract_file(path, file_id=f"sample:{sample_name}", filename=sample_name)
    return result.model_dump(mode="json")