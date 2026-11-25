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
    assert any("all payment dates match" in w.lower() for w in warnings)


def test_statement_total_requires_all_payment_dates_match_txn():
    from app.models.extraction_models import PaymentRow
    from app.services.extractors.utr_extractor import try_match_statement_total

    rows = [
        PaymentRow(
            payment_mode="NEFT",
            beneficiary_name="A",
            amount=Decimal("100.00"),
            payment_date="03-04-2026",
            utr_no="IN11111111111111",
            confidence=0.9,
            page_number=1,
        ),
        PaymentRow(
            payment_mode="NEFT",
            beneficiary_name="B",
            amount=Decimal("200.00"),
            payment_date="04-04-2026",  # different date
            utr_no="IN22222222222222",
            confidence=0.9,
            page_number=1,
        ),
    ]
    row, warnings = try_match_statement_total(
        rows,
        amount=Decimal("300.00"),
        txn_date="2026-04-03",
    )
    assert row is None
    assert any("not all payment dates" in w.lower() for w in warnings)


def test_statement_total_matches_when_all_dates_equal_txn():
    from app.models.extraction_models import PaymentRow
    from app.services.extractors.utr_extractor import try_match_statement_total

    rows = [
        PaymentRow(
            payment_mode="NEFT",
            beneficiary_name="CHINTALA ANJAIAH SAGAR",
            amount=Decimal("36000.00"),
            payment_date="03-04-2026",
            utr_no="IN42609357238928",
            confidence=0.9,
            page_number=1,
        ),
        PaymentRow(
            payment_mode="NEFT",
            beneficiary_name="LADRASINGH",
            amount=Decimal("8500.00"),
            payment_date="03-04-2026",
            utr_no="IN42609357238950",
            confidence=0.9,
            page_number=1,
        ),
        PaymentRow(
            payment_mode="NEFT",
            beneficiary_name="PRADHAN SINGH",
            amount=Decimal("8500.00"),
            payment_date="03-04-2026",
            utr_no="IN42609357238963",
            confidence=0.9,
            page_number=1,
        ),
    ]
    total = Decimal("53000.00")
    row, warnings = try_match_statement_total(
        rows,
        amount=total,
        txn_date="2026-04-03",
    )
    assert row is not None
    assert row.payment_mode == "STATEMENT"
    assert row.amount == total
    assert any("all payment dates match" in w.lower() for w in warnings)

    # Single-line amount that appears twice stays ambiguous (total does not match)
    amb_row, amb_warnings = select_matching_payment_row(
        rows,
        amount=Decimal("8500.00"),
        txn_date="2026-04-03",
    )
    assert amb_row is None
    assert any("multiple payment rows matched amount and date" in w.lower() for w in amb_warnings)