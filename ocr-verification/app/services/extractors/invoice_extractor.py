from __future__ import annotations

import re
from typing import Optional

from app.models.extraction_models import FieldValue
from app.services.extractors.amount_total import extract_grand_total
from app.validators.amount_validator import normalize_amount


def _find(pattern: str, text: str, flags=re.IGNORECASE | re.MULTILINE) -> Optional[str]:
    match = re.search(pattern, text, flags)
    if not match:
        return None
    return match.group(1).strip() if match.lastindex else match.group(0).strip()


def _normalize_ocr_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = value.strip()
    text = re.sub(r"(?i)^(?:invoice\s*)?date\s*[:\-]?\s*", "", text).strip()
    # OCR glitch: 08/11/2.6 -> 08/11/26
    text = re.sub(r"(\d{1,2}[/-]\d{1,2}[/-])(\d)\.(\d)", r"\1\2\3", text)
    # Prefer an already well-formed DD-MM-YYYY / DD/MM/YY
    match = re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", text)
    if match:
        return match.group(0)
    # Missing separator before short year: 08/1126 -> 08/11/26
    glued = re.sub(r"\b(\d{1,2})([/-])(\d{2})(\d{2})\b", r"\1\2\3\2\4", text)
    match = re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", glued)
    return match.group(0) if match else text


def extract_invoice_fields(text: str, page_number: int = 1, confidence: float = 0.0) -> dict:
    """
    Extract amount/date/party from tax invoices / commercial invoices.
    Prefers Grand Total / Total Amount After Tax over line or pre-tax totals.
    """
    warnings: list[str] = []
    fields: dict[str, FieldValue] = {}
    compact = re.sub(r"\s+", " ", text)

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

    doc_no = (
        _find(r"(?i)Invoice\s*No\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9/_\-]{2,})", compact)
        or _find(r"(?i)Inv\.?\s*No\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9/_\-]{2,})", compact)
    )
    doc_date = _normalize_ocr_date(
        _find(r"(?i)Invoice\s*Date\s*[:\-]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})", compact)
        or _find(r"(?i)\bDate\s*[:\-]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})", compact)
    )

    party = (
        _find(
            r"(?i)Billed\s*To\s+([A-Za-z][A-Za-z0-9 .,&'\-]{4,80})",
            compact,
        )
        or _find(
            r"(?i)(?:Shoee|Shree|Sri)\s+Swamin[a-z]*\s+Guruk[a-z]*[^\n]*",
            compact,
        )
    )

    # Payable grand total only (never line qty / tax components)
    amount_value, amount_source = extract_grand_total(text)
    if amount_value is None:
        warnings.append("Invoice grand total amount not found")

    description = _find(
        r"(?i)Particulars\s+.*?((?:A/?4|Printer|Charges)[A-Za-z0-9 \-/,.]{5,80})",
        compact,
    )

    fields["documentNumber"] = fv(doc_no, confidence if doc_no else 0.0, "invoice number not readable", "invoice_no")
    fields["documentDate"] = fv(
        doc_date, confidence if doc_date else 0.0, "invoice date not readable", "invoice_date"
    )
    fields["invoiceDate"] = fields["documentDate"]
    fields["partyName"] = fv(party, confidence if party else 0.0, "billed-to party not readable")
    fields["amount"] = fv(
        str(amount_value) if amount_value is not None else None,
        confidence if amount_value is not None else 0.0,
        "invoice grand total not readable",
        amount_source or "grand_total",
    )
    fields["description"] = fv(description, confidence if description else 0.0)

    return {"fields": fields, "items": [], "warnings": warnings}
