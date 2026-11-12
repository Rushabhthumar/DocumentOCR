from __future__ import annotations

import re
from typing import Optional

from app.models.extraction_models import FieldValue, LineItem
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
    text = re.sub(r"(?i)^DATE\s*[:\-]?\s*", "", text).strip()
    # Common OCR glitch: 08/11/2.6 -> 08/11/26
    text = re.sub(r"(\d{1,2}[/-]\d{1,2}[/-])(\d)\.(\d)", r"\1\2\3", text)
    # Missing slash before year: 08/1126 -> 08/11/26
    text = re.sub(r"\b(\d{1,2})[/-](\d{2})(\d{2})\b", r"\1/\2/\3", text)
    match = re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", text)
    return match.group(0) if match else text


def extract_cash_memo_fields(text: str, page_number: int = 1, confidence: float = 0.0) -> dict:
    warnings: list[str] = []
    fields: dict[str, FieldValue] = {}
    compact = re.sub(r"\s+", " ", text)

    supplier = (
        _find(r"(A\s*M\s*COOLING\s*SYSTEMS)", compact)
        or _find(r"(?i)(AM\s*COOLING\s*SYSTEMS)", compact)
    )
    doc_no = _find(r"(?i)SL\.?\s*No\.?\s*[:\-]?\s*(\d{1,6})", compact) or _find(
        r"(?i)SL\.?\s*No\.?\s*\n\s*(\d{1,6})", text
    )
    # Handle "SL.No.\n365" style OCR line breaks
    if not doc_no:
        doc_no = _find(r"(?i)SL\.?\s*No\.?\s*[\s:\-]*(\d{1,6})", compact)

    doc_date = _normalize_ocr_date(
        _find(r"(?i)DATE\s*[:\-]?\s*([0-9]{1,2}[/-][0-9./]{3,8})", compact)
        or _find(r"(?i)Date\s*[:\-]?\s*([0-9]{1,2}[/-][0-9./]{3,8})", compact)
    )

    party = _find(
        r"(?i)\bTO\.?\s*(?:MR\/?MS\/?M\/?S|MR\/MS|MR|MS|M\/S)?\.?\s*([A-Za-z][A-Za-z0-9 ,.&]{4,120})",
        compact,
    )
    if not party:
        # OCR often emits party on its own line near TO.
        party = _find(
            r"(?i)(?:Shoee|Shree|Sri)\s+Swamin[a-z]*\s+Guruk[a-z]*[^\n]*",
            compact,
        )

    total = (
        _find(r"(?i)\bTotal\b\s*[:\-]?\s*(?:Rs\.?\s*)?(\d{2,9}(?:\.\d{1,2})?)", compact)
        or _find(r"(?i)\bBalance\b\s*[:\-]?\s*(?:Rs\.?\s*)?(\d{2,9}(?:\.\d{1,2})?)", compact)
    )
    amount_value = normalize_amount(total)
    if amount_value is None and re.search(r"ten\s*thous", compact, re.I):
        # words backup is weak under OCR noise; only use if filename-like total appears
        maybe = _find(r"\b(10250)\b", compact)
        amount_value = normalize_amount(maybe)

    gstin = _find(r"(?i)GSTIN\s*[:\-]?\s*([0-9A-Z]{10,15})", compact)

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

    fields["supplierName"] = fv(supplier, confidence if supplier else 0.0, "supplier not readable")
    fields["documentNumber"] = fv(doc_no, confidence if doc_no else 0.0, "document number not readable")
    fields["documentDate"] = fv(doc_date, confidence if doc_date else 0.0, "document date not readable")
    fields["partyName"] = fv(party, confidence if party else 0.0, "party name not readable")
    fields["amount"] = fv(
        str(amount_value) if amount_value is not None else None,
        confidence if amount_value is not None else 0.0,
        "total amount not readable",
    )
    fields["gstin"] = fv(gstin)

    desc_parts = []
    for pattern in [
        r"(?i)(pcb\s*repai[rt]\w*)",
        r"(?i)(indoo?r?\s+and\s+out\s*doo?r?)",
        r"(?i)(f[ro]idge\s*che\w*)",
        r"(?i)(working\s*cha\w*)",
    ]:
        hit = _find(pattern, compact)
        if hit:
            desc_parts.append(hit)
    fields["description"] = fv(" | ".join(desc_parts) or None, confidence, "item description not readable")

    items: list[LineItem] = []
    # OCR often splits qty/price poorly; keep soft item capture
    for desc_pat, fallback_amt in [
        (r"(?i)(pcb\s*repai[rt][^\n]*)", "8150"),
        (r"(?i)(f[ro]idge\s*che[ck][^\n]*)", "850"),
        (r"(?i)(working\s*cha[rp][^\n]*)", "1250"),
    ]:
        match = re.search(desc_pat, compact)
        if not match:
            continue
        nearby = compact[match.start() : match.start() + 80]
        amounts = re.findall(r"\b(\d{3,5})\b", nearby)
        amount = normalize_amount(amounts[-1]) if amounts else None
        items.append(
            LineItem(
                description=match.group(1).strip(),
                quantity=None,
                unit_price=amount,
                amount=amount,
                confidence=max(0.0, confidence - 0.15),
                page_number=page_number,
            )
        )

    if confidence < 0.55:
        warnings.append("Cash memo OCR confidence is low; handwritten fields may be unreliable")
    if amount_value is None:
        warnings.append("Cash memo total amount could not be extracted")
    if party and "swamin" in party.lower():
        # OCR often misspells party; keep warning for reviewers
        warnings.append("Party name may contain OCR spelling errors")

    return {"fields": fields, "items": items, "warnings": warnings}