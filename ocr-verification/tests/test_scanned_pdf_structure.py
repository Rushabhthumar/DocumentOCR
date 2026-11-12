from pathlib import Path

from app.services.pdf_service import PdfService

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
SCANNED = SAMPLES / "HYD_OtherType_2026-2027_10250.pdf"


def test_scanned_pdf_requires_ocr_per_page():
    pages = PdfService().process_pdf(SCANNED)
    assert len(pages) == 2
    for page in pages:
        assert page.mode.value == "OCR"
        assert page.image is not None
        assert not PdfService.is_meaningful_text(page.text)