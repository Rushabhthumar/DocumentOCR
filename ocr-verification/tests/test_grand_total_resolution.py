from decimal import Decimal

from app.models.document_models import DocumentType
from app.models.extraction_models import ExtractionResult
from app.services.extractors.amount_total import extract_grand_total
from app.services.extraction_service import ExtractionService

INVOICE_PAGE = """
Tax Invoice
OM SHREE KARNI TILES
Invoice No. OSK/26-27/132
Date: 08/11/2026
Quantity 5104.87 Sqf
Taxable 2,37,938.86
CGST 21,414.5
SGST 21,414.5
Sub Total : 2,80,767.85
Balance
:
 2,80,768.00
TOTAL 2,37,938.86
"""

EWAY_PAGE = """
e-Way Bill
Eway Bil No. 182608353954
Generated Date:08/11/2026
Tot. Taxable Amt : 237938.86
CGST Amt : 21414.50
SGST Amt: 21414.50
Total Inv. Amt: 280768.00
"""

GRN_PAGE = """
Goods Received Note (GRN)
GRN No. 2224
Date: 08/11/26
Material Quantity: 5104.87 59E
"""


def test_extract_grand_total_prefers_balance_not_quantity():
    amount, _ = extract_grand_total(INVOICE_PAGE)
    assert amount == Decimal("280768.00")


def test_extract_eway_total_inv_amt():
    amount, _ = extract_grand_total(EWAY_PAGE)
    assert amount == Decimal("280768.00")


def test_multi_page_bill_plus_eway_not_summed():
    svc = ExtractionService.__new__(ExtractionService)
    result = ExtractionResult(
        file_id="ref",
        document_type=DocumentType.UNKNOWN,
        page_count=3,
        extraction_status="SUCCESS",
        page_texts={1: INVOICE_PAGE, 2: EWAY_PAGE, 3: GRN_PAGE},
        page_types={
            1: DocumentType.INVOICE,
            2: DocumentType.EWAY_BILL,
            3: DocumentType.GRN,
        },
        warnings=[],
    )
    svc._populate_structured_fields(result, {1: 0.9, 2: 0.9, 3: 0.9})
    assert result.fields["amount"].value == "280768.00"
    assert result.document_type == DocumentType.INVOICE
    joined = " ".join(result.warnings)
    assert "not summed" in joined.lower() or "matches bill" in joined.lower()
    assert "5104.87" not in (result.fields["amount"].value or "")
