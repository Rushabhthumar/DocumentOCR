from decimal import Decimal
from pathlib import Path

from app.services.extractors.utr_extractor import (
    extract_payment_rows,
    select_matching_payment_row,
)
from app.services.pdf_service import PdfService


SAMPLES = Path(__file__).resolve().parents[1] / "samples"
PAYMENT_PDF = SAMPLES / "HYD_Payment_UTRStatement.pdf"


def test_digital_pdf_text_extraction_not_ocr():
    pages = PdfService().process_pdf(PAYMENT_PDF)
    assert len(pages) == 1
    assert pages[0].mode.value == "TEXT"
    assert "UTR" in pages[0].text.upper()
    assert pages[0].image is None


def test_utr_rows_preserve_wrapped_remarks():
    text = PdfService().process_pdf(PAYMENT_PDF)[0].text
    rows = extract_payment_rows(text)
    assert len(rows) >= 20
    first = next(r for r in rows if r.utr_no == "IN42631151879720")
    assert first.beneficiary_name and "JATOTH" in first.beneficiary_name.upper()
    assert first.amount == Decimal("85850.00")
    assert first.payment_date.replace("/", "-") in {"07-11-2026", "07/11/2026"}
    assert first.remark and "WOOD" in first.remark.upper()

    wrapped = next(r for r in rows if r.utr_no == "IN42631150809691")
    assert wrapped.amount == Decimal("36000.00")
    assert "SALARY" in (wrapped.remark or "").upper()


def test_select_row_by_amount_and_date():
    text = PdfService().process_pdf(PAYMENT_PDF)[0].text
    rows = extract_payment_rows(text)
    row, warnings = select_matching_payment_row(
        rows,
        amount=Decimal("85850"),
        txn_date="2026-11-07T00:00:00Z",
    )
    assert row is not None
    assert row.utr_no == "IN42631151879720"


def test_incorrect_amount_does_not_match_row():
    text = PdfService().process_pdf(PAYMENT_PDF)[0].text
    rows = extract_payment_rows(text)
    row, warnings = select_matching_payment_row(
        rows,
        amount=Decimal("1.00"),
        txn_date="2026-11-07T00:00:00Z",
    )
    assert row is None
    assert any("amount" in w.lower() for w in warnings)


def test_statement_total_matches_sum_of_rows():
    text = PdfService().process_pdf(PAYMENT_PDF)[0].text
    rows = extract_payment_rows(text)
    total = sum((r.amount or Decimal("0") for r in rows), Decimal("0"))
    row, warnings = select_matching_payment_row(
        rows,
        amount=total,
        txn_date="2026-11-07T00:00:00Z",
    )
    assert row is not None
    assert row.amount == total
    assert row.payment_mode == "STATEMENT"
    assert any("statement total" in w.lower() for w in warnings)