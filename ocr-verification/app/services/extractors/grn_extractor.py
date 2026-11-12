from __future__ import annotations

import re
from typing import Optional

from app.models.extraction_models import FieldValue, LineItem


def _find(pattern: str, text: str) -> Optional[str]:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip() if match.lastindex else match.group(0).strip()


def extract_grn_fields(text: str, page_number: int = 1, confidence: float = 0.0) -> dict:
    warnings: list[str] = []
    fields: dict[str, FieldValue] = {}

    def fv(value, conf=confidence, reason=None, source=None):
        if value is None or value == "":
            return FieldValue(
                value=None,
                confidence=0.0,
                page_number=page_number,
                reason=reason or "not found",
                source=source,
            )
        return FieldValue(value=value, confidence=conf, page_number=page_number, source=source)

    compact = re.sub(r"\s+", " ", text)
    grn_no = _find(r"(?i)GRN\s*No\.?\s*[:\-]?\s*(\d{2,})", compact)
    gate_entry = _find(r"(?i)Gate\s*Entry\s*No\.?\s*[:\-]?\s*(\d{2,})", compact)
    doc_date = _find(r"(?i)\bDate\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", compact)
    supplier = _find(r"(?i)Supplier\s*Name\s*[:\-]?\s*([A-Za-z][A-Za-z0-9 .,&]{2,80})", compact)
    invoice_no = _find(r"(?i)Supplier\s*Invoice\s*No\.?\s*[:\-]?\s*(\d{3,})", compact)
    invoice_date = _find(r"(?i)Invoice\s*Date\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", compact)
    material = _find(r"(?i)Material\s*Description\s*[:\-]?\s*([A-Za-z][A-Za-z0-9 .,&/\-]{2,80})", compact)
    qty = _find(r"(?i)Material\s*Quantity\s*[:\-]?\s*([0-9]{1,4}\s*Nos\.?)", compact)
    remark = _find(r"(?i)Remark\s*[:\-]?\s*([A-Za-z][^\n]{3,120})", text)
    department = None
    if re.search(r"(?i)Kitchen", text):
        department = "Kitchen"

    fields["documentNumber"] = fv(grn_no, source="grn_no")
    fields["gateEntryNo"] = fv(gate_entry, source="gate_entry_no")
    fields["documentDate"] = fv(doc_date or invoice_date, source="grn_date")
    fields["partyName"] = fv(supplier, source="supplier_name", reason="supplier name not readable")
    fields["supplierInvoiceNo"] = fv(invoice_no, source="supplier_invoice_no")
    fields["invoiceDate"] = fv(invoice_date, source="invoice_date")
    fields["description"] = fv(remark or material, source="remark_or_material")
    fields["quantity"] = fv(qty, source="material_quantity")
    fields["department"] = fv(department, source="department")
    # GRN typically has no monetary total
    fields["amount"] = FieldValue(
        value=None,
        confidence=0.0,
        page_number=page_number,
        reason="GRN does not contain a monetary grand total",
        source="amount",
    )

    items = []
    if material:
        items.append(
            LineItem(
                description=material,
                quantity=qty,
                amount=None,
                confidence=confidence,
                page_number=page_number,
            )
        )

    warnings.append("GRN extracted without amount; amount verification must rely on another document")
    if confidence < 0.55:
        warnings.append("GRN OCR confidence is low; handwritten fields may be unreliable")

    return {"fields": fields, "items": items, "warnings": warnings}