"""Re-OCR failing live PDFs with current pipeline + Hindi fallback experiment."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.services.extraction_service import ExtractionService
from app.services.ocr_service import OcrService
from app.services.pdf_service import PdfService

FILES = {
    "11656": "7790.00",
    "11657": "13465.00",
    "11659": "15480.00",
    "11663": "3903.00",
    "11660": "112000.00",
}


def main() -> None:
    get_settings.cache_clear()
    settings = get_settings()
    settings.enable_structure_ocr = True
    settings.enable_trocr_fallback = True
    settings.structure_ocr_for_all_pages = False
    svc = ExtractionService(settings=settings)
    out = ROOT / "temp" / "reocr"
    out.mkdir(parents=True, exist_ok=True)

    for fid, expected in FILES.items():
        path = ROOT / "temp" / f"live_{fid}.pdf"
        print("=" * 80, fid, "expected", expected)
        result = svc.extract_file(path, file_id=fid, filename=path.name)
        fields = {k: v.value for k, v in result.fields.items()}
        summary = {
            "type": result.document_type.value if result.document_type else None,
            "fields": fields,
            "warnings": result.warnings[:12],
            "build": (result.metadata or {}).get("extractor_build"),
            "raw": (result.raw_text or "")[:1500],
        }
        (out / f"{fid}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print("got amount", fields.get("amount"), "date", fields.get("documentDate"), "type", summary["type"])
        print("raw head:", summary["raw"][:500].replace("\n", " | "))


if __name__ == "__main__":
    main()
