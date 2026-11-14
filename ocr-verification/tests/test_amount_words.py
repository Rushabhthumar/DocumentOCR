from decimal import Decimal

from app.services.extractors.amount_total import extract_grand_total
from app.services.extractors.amount_words import extract_amount_from_words, words_to_amount
from app.services.extractors.ocr_date import normalize_ocr_date
from app.validators.date_validator import normalize_date


def test_words_to_amount_ocr_misspellings():
    assert words_to_amount("seveerteen Jhousand rwo hundred ony") == Decimal("17200.00")
    assert words_to_amount("froordeen thousand fourteen only") == Decimal("14014.00")
    assert words_to_amount("Twenty Seven Thousand Nine Hundred Sixty Rupees") == Decimal(
        "27960.00"
    )


def test_extract_amount_keeps_tens_after_hundred():
    text = (
        "Total Amount (in words) Twenty Seven Thousand Nine Hundred Sixty Rupees "
        "terms and Conditions"
    )
    assert extract_amount_from_words(text) == Decimal("27960.00")


def test_grand_total_prefers_amount_in_words_when_digits_wrong():
    amount, source = extract_grand_total("TOTAL 12,200\nseveerteen Jhousand rwo hundred ony/-")
    assert amount == Decimal("17200.00")
    assert source and "words" in source


def test_ocr_date_repairs():
    assert normalize_date("31/10/226").isoformat() == "2026-10-31"
    assert normalize_ocr_date("10.11.2026") == "10/11/2026"
    assert normalize_date("08/11/26").isoformat() == "2026-11-08"
    assert normalize_date("07 t04/2026").isoformat() == "2026-04-07"
    assert normalize_date("6-Apr-26").isoformat() == "2026-04-06"
    assert normalize_date("01-4.26").isoformat() == "2026-04-01"
    assert normalize_date("01-4/26").isoformat() == "2026-04-01"
    assert normalize_date("Date. 184126").isoformat() == "2026-04-18"
    assert normalize_date("Date.0.20626").isoformat() == "2026-06-02"


def test_cash_memo_equals_paise_amount():
    from app.validators.amount_validator import normalize_amount
    from app.services.extractors.amount_total import extract_grand_total

    assert normalize_amount("3900=00") == Decimal("3900.00")
    assert normalize_amount("3900=0") == Decimal("3900.00")
    amount, _ = extract_grand_total("TOTAL 3900=0\nFor, Sumit Enterprises")
    assert amount == Decimal("3900.00")


def test_invoice_prefers_invoice_date_not_due_date():
    from app.services.extractors.invoice_extractor import extract_invoice_fields

    text = """
    INVOICE
    Due Date 14/04/2026
    Invoice Date 07 t04/2026
    Total Amount (in words) Twenty Seven Thousand Nine Hundred Sixty Rupees
    27,960
    """
    parsed = extract_invoice_fields(text, confidence=0.9)
    assert parsed["fields"]["documentDate"].value in {"07/04/2026", "07-04-2026"}
    assert parsed["fields"]["amount"].value == "27960.00"


def test_extract_amount_from_words_near_total_label():
    assert extract_amount_from_words(
        "Invoice Amount in Words Seven Thousand Six Hundred Seventy Rupees Only"
    ) == Decimal("7670.00")


def test_cash_memo_amount_column_and_bad_date_not_invented():
    from app.services.extractors.cash_memo_extractor import extract_cash_memo_fields
    from app.validators.date_validator import normalize_date

    text = """
    CASHMEMO
    No:27
    Date: 12 t (/2026
    Amount
    BIOCK
    3000
    9000
    900oL
    """
    parsed = extract_cash_memo_fields(text, confidence=0.8)
    assert parsed["fields"]["amount"].value == "9000.00"
    # Month unreadable — do not invent 12/20/26
    assert normalize_date("12 t (/2026") is None
