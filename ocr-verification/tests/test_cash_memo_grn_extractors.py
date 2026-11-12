from app.services.extractors.cash_memo_extractor import extract_cash_memo_fields
from app.services.extractors.grn_extractor import extract_grn_fields


def test_cash_memo_extractor_from_text():
    text = """
    CASH MEMO
    A M COOLING SYSTEMS
    GSTIN: 36ERSPS0176QIZN
    SL.No. 365
    Date: 08/11/26
    TO. MR/MS Shree Swaminarayan Gurukul, Hyderabad.
    pcb Repair Indoor and out Door 1 8150 8150
    fridge checking 1 850 850
    Working charges 1 1250 1250
    Total 10250
    Balance 10250
    Ten thousand two hundred Fifty only/-
    """
    parsed = extract_cash_memo_fields(text, page_number=1, confidence=0.8)
    assert parsed["fields"]["documentNumber"].value == "365"
    assert parsed["fields"]["documentDate"].value == "08/11/26"
    assert parsed["fields"]["amount"].value == "10250.00"
    assert "Swaminarayan" in (parsed["fields"]["partyName"].value or "")
    assert len(parsed["items"]) >= 1


def test_grn_extractor_has_no_amount():
    text = """
    Goods Received Note (GRN)
    Date: 08/11/26
    GRN No.: 2225
    Gate Entry No.: 2225
    Supplier Name: Am Cooling Systems
    Supplier Invoice No.: 365
    Invoice Date: 08/11/2026
    Material Description: PCB Repair
    Material Quantity: 3 Nos.
    Remark: PCB Repair indoor & outdoor for kitchen
    Kitchen
    """
    parsed = extract_grn_fields(text, page_number=2, confidence=0.7)
    assert parsed["fields"]["documentNumber"].value == "2225"
    assert parsed["fields"]["partyName"].value
    assert parsed["fields"]["amount"].value is None
    assert any("no monetary" in w.lower() or "without amount" in w.lower() for w in parsed["warnings"])