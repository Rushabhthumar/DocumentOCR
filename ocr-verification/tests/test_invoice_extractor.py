from app.services.extractors.invoice_extractor import extract_invoice_fields
from app.validators.date_validator import normalize_date


SAMPLE = """
TAX INVOICE
AM ENTERPRISES
Billed To
Shree Swaminarayan Gurukul Hyd
Invoice No: AE/26-27/021
Invoice Date :06-11-2026
Total Amount Before Tax 6,500.00
CGST @ 9% 585.00
SGST @ 9% 585.00
Tax Amount GST : 1,170.00
Total Amount After Tax 7,670.00
Grand Total 7,670.00
"""


def test_invoice_extracts_grand_total_and_date():
    parsed = extract_invoice_fields(SAMPLE, confidence=0.9)
    assert parsed["fields"]["amount"].value == "7670.00"
    assert parsed["fields"]["documentDate"].value == "06-11-2026"
    assert "AE/26-27/021" in (parsed["fields"]["documentNumber"].value or "")


def test_finance_iso_date_uses_ist_calendar_day():
    # 2026-11-05T18:30:00Z == 2026-11-06 00:00 IST (UI date 06-11-2026)
    assert normalize_date("2026-11-05T18:30:00Z").isoformat() == "2026-11-06"
