"""
Demonstrate extraction on the two sample PDFs.
Distinguishes actual extracted values (digital PDF) from OCR-dependent values.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.extraction_service import ExtractionService
from app.services.extractors.utr_extractor import extract_payment_rows
from app.services.pdf_service import PdfService


def main() -> None:
    samples = ROOT / "samples"
    payment = samples / "HYD_Payment_UTRStatement.pdf"
    other = samples / "HYD_OtherType_2026-2027_10250.pdf"

    print("=== Sample 2: Payment/UTR (digital text, actual extraction) ===")
    pages = PdfService().process_pdf(payment)
    text = pages[0].text
    rows = extract_payment_rows(text)
    preview = [
        {
            "beneficiary_name": r.beneficiary_name,
            "amount": str(r.amount) if r.amount is not None else None,
            "payment_date": r.payment_date,
            "remark": r.remark,
            "status": r.status,
            "utr_no": r.utr_no,
        }
        for r in rows[:5]
    ]
    print(json.dumps({"page_mode": pages[0].mode.value, "row_count": len(rows), "first_rows": preview}, indent=2))

    print("\n=== Sample 1: Cash Memo + GRN (scanned; OCR required) ===")
    scanned_pages = PdfService().process_pdf(other)
    print(
        json.dumps(
            {
                "page_count": len(scanned_pages),
                "modes": [p.mode.value for p in scanned_pages],
                "note": "Selectable text is empty; structured values require PaddleOCR.",
            },
            indent=2,
        )
    )
    try:
        result = ExtractionService().extract_file(
            other,
            file_id="sample:other",
            filename=other.name,
        )
        print("OCR extraction status:", result.extraction_status)
        print(
            json.dumps(
                {
                    "documentType": result.document_type.value,
                    "fields": {k: v.model_dump() for k, v in result.fields.items()},
                    "warnings": result.warnings,
                    "errors": result.errors,
                },
                indent=2,
            )
        )
    except Exception as exc:  # noqa: BLE001
        print("OCR not available yet:", exc)
        print("Install paddlepaddle + paddleocr (see README), then re-run this script.")


if __name__ == "__main__":
    main()