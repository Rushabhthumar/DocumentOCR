from __future__ import annotations

import re
from typing import Optional

from app.models.extraction_models import FieldValue
from app.services.extractors.amount_total import extract_grand_total
from app.services.extractors.ocr_date import normalize_ocr_date


def _find(pattern: str, text: str, flags=re.IGNORECASE | re.MULTILINE) -> Optional[str]:
    match = re.search(pattern, text, flags)
    if not match:
        return None
    return match.group(1).strip() if match.lastindex else match.group(0).strip()


_BAD_PARTY = re.compile(
    r"(?i)(?:impeccable|condition|hsn|sac\s*code|particulars|quantity|rate|"
    r"amount|invoice\s*details|ship\s*to|transport|vehicle)"
)


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
    # Reject values that are clearly dates mistaken as invoice numbers
    if doc_no:
        from app.validators.date_validator import normalize_date

        if normalize_date(doc_no) is not None and not re.search(r"[A-Za-z]", doc_no):
            doc_no = None
        elif re.fullmatch(r"\d{1,2}[-/]\d{1,2}[-/]?\d{0,4}", doc_no):
            doc_no = None
        elif re.fullmatch(r"\d{1,2}[-/]\d{5,8}", doc_no):
            # Glued OCR date like 8-112026 mistaken for invoice no
            doc_no = None

    # Prefer Invoice Date over Due Date. OCR often emits "lnvoice" and "07 t04/2026".
    doc_date = normalize_ocr_date(
        _find(
            r"(?i)l?nvoice\s*Date\s*[:\-]?\s*([0-9]{1,2}\s*[tT./\-]\s*[0-9]{1,2}[./\-][0-9]{2,4})",
            compact,
        )
        or _find(
            r"(?i)(?:bill\s*date|dated)\s*[:\-]?\s*"
            r"([0-9]{1,2}[./\-][0-9]{1,2}[./\-][0-9]{2,4}"
            r"|[0-9]{1,2}[-/\s]+[A-Za-z]{3,9}[-/\s]+[0-9]{2,4})",
            compact,
        )
    )
    # Never take Due Date as document date
    if not doc_date:
        due = _find(
            r"(?i)due\s*date\s*[:\-]?\s*([0-9]{1,2}[./\-][0-9]{1,2}[./\-][0-9]{2,4})",
            compact,
        )
        generic = _find(
            r"(?i)\bDate\s*[:\-]?\s*"
            r"([0-9]{1,2}[./\-][0-9]{1,2}[./\-][0-9]{2,4}"
            r"|[0-9]{1,2}[-/\s]+[A-Za-z]{3,9}[-/\s]+[0-9]{2,4})",
            compact,
        )
        # Printed "Dated 6-Apr-26" beside invoice no (Satyam bill layout)
        if not generic:
            generic = _find(
                r"(?i)\bdated\b\s*[:\-]?\s*"
                r"([0-9]{1,2}[-/\s]+[A-Za-z]{3,9}[-/\s]+[0-9]{2,4})",
                compact,
            )
        if not generic:
            generic = _find(
                r"(?i)\b([0-9]{1,2}[-/\s]+[A-Za-z]{3,9}[-/\s]+[0-9]{2,4})\b",
                compact,
            )
        if generic and (not due or generic != due):
            doc_date = normalize_ocr_date(generic)

    # Prefer explicit Gurukul / buyer name over adjacent HSN labels
    party = _find(
        r"(?i)((?:Shoee|Shree|Sri)\s+Swamin[a-z]*\s+Guruk[a-z]*[^\n]{0,40})",
        compact,
    ) or _find(
        r"(?i)(?:Billed\s*To|Bill\s*To|Buyer|Sold\s*To)\s*[:\-]?\s*"
        r"(?:HSN\s*&?\s*SAC\s*Code\s*[:\-]?\s*[0-9A-Z]+\s*)?"
        r"([A-Za-z][A-Za-z0-9 .,&'\-]{4,80})",
        compact,
    )
    if party:
        party = re.sub(r"(?i)^HSN\s*&?\s*SAC\s*Code\s*[:\-]?\s*[0-9A-Z]+\s*", "", party).strip()
        party = re.split(r"(?i)\b(?:invoice\s*no|invoice\s*date|gst|ship\s*to|hsn)\b", party)[0].strip(" .,:-")
        if _BAD_PARTY.search(party) or len(party) < 4:
            party = _find(
                r"(?i)((?:Shoee|Shree|Sri)\s+Swamin[a-z]*\s+Guruk[a-z]*[^\n]{0,40})",
                compact,
            )

    amount_value, amount_source = extract_grand_total(text)
    if amount_value is None:
        warnings.append("Invoice grand total amount not found")

    description = _find(
        r"(?i)Particulars\s+.*?((?:A/?4|Printer|Charges|Robo|Sand|Tile)[A-Za-z0-9 \-/,.]{5,80})",
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
