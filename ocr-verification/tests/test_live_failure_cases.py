"""Regression tests for the exact OCR texts that failed in live verification history."""

from decimal import Decimal

from app.models.document_models import DocumentType
from app.models.extraction_models import ExtractionResult
from app.services.extraction_service import ExtractionService


def _run(page_texts: dict[int, str], page_types: dict[int, DocumentType] | None = None):
    svc = ExtractionService.__new__(ExtractionService)
    result = ExtractionResult(
        file_id="t",
        document_type=DocumentType.UNKNOWN,
        page_count=len(page_texts),
        extraction_status="SUCCESS",
        page_texts=page_texts,
        page_types=page_types or {k: DocumentType.UNKNOWN for k in page_texts},
        metadata={},
    )
    svc._populate_structured_fields(result, {k: 0.9 for k in page_texts})
    return result


def test_cash_bill_prefers_amount_in_words_over_ocr_total():
    text = """
    CASH BILL
    ENGINEERING WORKS
    Date 11/11/2026
    TOTAL
    12,200
    ADV.
    BAL
    seveerteen Jhousand rwo hundred
    ony/-
    """
    result = _run({1: text})
    assert result.fields["amount"].value == "17200.00"
    assert result.document_type == DocumentType.CASH_MEMO


def test_invoice_plus_grn_uses_bill_total_not_grn_qty():
    pages = {
        1: """
        TAX INVOICE
        Invoice No. 17/2026-77
        Invoice Date: 10.11.2026
        TOTAL BILL AMOUNT
        10,966.00
        Invoice Amount (In Words: Ten Thousand Nine Hundred Sixty Six Only)
        """,
        2: """
        Goods Received Note (GRN)
        GRN NO. 2228
        Material Quantity:
        7607/-0215
        Remark: RoboSana
        """,
    }
    result = _run(
        pages,
        {1: DocumentType.INVOICE, 2: DocumentType.GRN},
    )
    assert result.fields["amount"].value == "10966.00"
    assert result.document_type == DocumentType.INVOICE


def test_cash_memo_uses_amount_column_not_bank_account():
    text = """
    CASHMEMO
    No:27
    Date: 12/11/2026
    Amount
    3000
    9000
    900oL
    BANK:IOBBANK,A/cNo:863510110015140
    IFSC Code:BKID0008635
    PAN No :ADLP12015F
    """
    result = _run({1: text})
    assert result.fields["amount"].value == "9000.00"
    assert Decimal(result.fields["amount"].value) != Decimal("12015.00")


def test_laundry_cash_bill_ignores_register_pages():
    pages = {
        1: """
        CASH BILL
        MANIKANTA LAUNDRY
        Date:31/10/226
        TOTAL
        14012
        frousdeen thousand or.
        froordeen onyl
        """,
        2: """
        LAUNDARY REGISTER
        Monitor's Sign
        61
        53
        """,
    }
    result = _run({1: pages[1], 2: pages[2]})
    assert result.fields["amount"].value == "14014.00"
