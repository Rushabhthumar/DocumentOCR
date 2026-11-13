from app.models.document_models import DocumentType
from app.services.document_classifier import DocumentClassifier
from app.services.extractors.generic_extractor import extract_generic_fields
from app.services.extraction_service import ExtractionService
from app.models.extraction_models import ExtractionResult


TAX_INVOICE = """
ORIGINAL
GSTIN No : 36BCCPM8267Q1ZR
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

RECEIPT = """
MONEY RECEIPT
Receipt No: R-102
Date: 12/10/2026
Received with thanks from Shree Gurukul
Amount Rs. 5,500.00
"""

UNKNOWN_BILL = """
SUPPLIER COPY
Bill No AB-99
Dated 05-11-2026
Party Name: Local Store
Net Payable 3200.50
"""


def test_generic_extracts_tax_invoice_total_and_date():
    parsed = extract_generic_fields(TAX_INVOICE, confidence=0.9)
    assert parsed["fields"]["amount"].value == "7670.00"
    assert parsed["fields"]["documentDate"].value == "06-11-2026"


def test_generic_extracts_receipt_and_unknown_bill():
    receipt = extract_generic_fields(RECEIPT)
    assert receipt["fields"]["amount"].value == "5500.00"
    assert receipt["fields"]["documentDate"].value == "12/10/2026"

    bill = extract_generic_fields(UNKNOWN_BILL)
    assert bill["fields"]["amount"].value == "3200.50"
    assert bill["fields"]["documentDate"].value == "05-11-2026"


def test_classifier_recognizes_invoice_receipt_voucher():
    clf = DocumentClassifier()
    assert clf.classify_page_text(TAX_INVOICE).document_type == DocumentType.INVOICE
    assert clf.classify_page_text(RECEIPT).document_type == DocumentType.RECEIPT
    voucher = clf.classify_page_text("Payment Voucher Voucher No V-1 Voucher Date 01-01-2026")
    assert voucher.document_type == DocumentType.VOUCHER


def test_extraction_service_fills_unknown_pages():
    svc = ExtractionService.__new__(ExtractionService)
    result = ExtractionResult(
        file_id="x",
        document_type=DocumentType.UNKNOWN,
        page_count=1,
        extraction_status="SUCCESS",
        page_texts={1: UNKNOWN_BILL},
        page_types={1: DocumentType.UNKNOWN},
    )
    svc._populate_structured_fields(result, {1: 0.9})
    assert result.fields["amount"].value == "3200.50"
    assert result.fields["documentDate"].value == "05-11-2026"
