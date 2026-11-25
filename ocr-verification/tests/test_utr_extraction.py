from decimal import Decimal
from pathlib import Path

from app.models.extraction_models import PaymentRow
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


def test_select_row_by_date_amount_and_remark():
    text = PdfService().process_pdf(PAYMENT_PDF)[0].text
    rows = extract_payment_rows(text)
    target = next(r for r in rows if r.utr_no == "IN42631151879720")
    row, warnings = select_matching_payment_row(
        rows,
        amount=Decimal("85850"),
        txn_date="2026-11-07T00:00:00Z",
        narration=target.remark,
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
        narration="ANY NARRATION",
    )
    assert row is None
    assert any("amount" in w.lower() for w in warnings)


def test_statement_sum_is_not_used_for_matching():
    """Multi-row UTR docs must not verify by summing all row amounts."""
    text = PdfService().process_pdf(PAYMENT_PDF)[0].text
    rows = extract_payment_rows(text)
    total = sum((r.amount or Decimal("0") for r in rows), Decimal("0"))
    row, warnings = select_matching_payment_row(
        rows,
        amount=total,
        txn_date="2026-11-07T00:00:00Z",
        narration="STATEMENT TOTAL SHOULD NOT MATCH",
    )
    assert row is None
    assert not any("statement total" in w.lower() for w in warnings)


def test_remark_disambiguates_same_date_and_amount():
    rows = [
        PaymentRow(
            payment_mode="NEFT",
            beneficiary_name="LADRASINGH",
            amount=Decimal("8500.00"),
            payment_date="03-04-2026",
            remark="PAID TO LADRASINGH FOR SALARY",
            utr_no="IN42609357238950",
            confidence=0.9,
            page_number=1,
        ),
        PaymentRow(
            payment_mode="NEFT",
            beneficiary_name="PRADHAN SINGH",
            amount=Decimal("8500.00"),
            payment_date="03-04-2026",
            remark="PAID TO PRADHAN SINGH FOR SALARY",
            utr_no="IN42609357238963",
            confidence=0.9,
            page_number=1,
        ),
    ]
    row, warnings = select_matching_payment_row(
        rows,
        amount=Decimal("8500.00"),
        txn_date="2026-04-03",
        narration="PAID TO PRADHAN SINGH FOR SALARY",
    )
    assert row is not None
    assert row.utr_no == "IN42609357238963"


def test_date_amount_match_but_remark_mismatch_fails():
    rows = [
        PaymentRow(
            payment_mode="NEFT",
            beneficiary_name="MIHIRKUMAR D BOGHARA",
            amount=Decimal("3450.00"),
            payment_date="03-08-2026",
            remark="PAID TO MIHIRKUMAR D BOGHARA FOR MIHIR BHAI CAR PETROL",
            utr_no="IN42621557017544",
            confidence=0.9,
            page_number=1,
        ),
    ]
    row, warnings = select_matching_payment_row(
        rows,
        amount=Decimal("3450.00"),
        txn_date="2026-08-03",
        narration="COMPLETELY DIFFERENT NARRATION XYZ",
    )
    assert row is None
    assert any("remark" in w.lower() for w in warnings)


def test_wrong_transaction_date_fails_with_reason():
    rows = [
        PaymentRow(
            payment_mode="NEFT",
            beneficiary_name="M S MOTORS",
            amount=Decimal("10590.00"),
            payment_date="03-08-2026",
            remark="PAID TO M S MOTORS FOR EHGINE OIL",
            utr_no="IN42621557017900",
            confidence=0.9,
            page_number=1,
        ),
    ]
    row, warnings = select_matching_payment_row(
        rows,
        amount=Decimal("10590.00"),
        txn_date="2026-08-04",
        narration="PAID TO M S MOTORS FOR EHGINE OIL",
    )
    assert row is None
    assert any("transaction date" in w.lower() for w in warnings)
