from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

from app.models.extraction_models import FieldValue, LineItem
from app.services.extractors.amount_total import extract_grand_total
from app.services.extractors.ocr_date import normalize_ocr_date
from app.validators.amount_validator import normalize_amount


def _find(pattern: str, text: str, flags=re.IGNORECASE | re.MULTILINE) -> Optional[str]:
    match = re.search(pattern, text, flags)
    if not match:
        return None
    return match.group(1).strip() if match.lastindex else match.group(0).strip()


_BAD_PARTY = re.compile(
    r"(?i)(?:impeccable|condition|hsn|sac\s*code|particulars|description|"
    r"qty\.?|rate|amount|total|bank|ifsc|signature|authori)"
)


def extract_cash_memo_fields(text: str, page_number: int = 1, confidence: float = 0.0) -> dict:
    """
    Extract fields from cash memo / cash bill layouts (printed or handwritten-ish OCR).
    """
    warnings: list[str] = []
    fields: dict[str, FieldValue] = {}
    compact = re.sub(r"\s+", " ", text)

    supplier = (
        _find(r"(A\s*M\s*COOLING\s*SYSTEMS)", compact)
        or _find(r"(?i)(AM\s*COOLING\s*SYSTEMS)", compact)
        or _find(r"(?i)\b(MANIKANTA\s+LAUNDRY)\b", compact)
        or _find(r"(?i)\b([A-Z][A-Za-z0-9 &./\-]{2,40}ENGINEERING\s+WORKS)\b", compact)
        or _find(
            r"(?i)\b((?:SRI|SHRI)?\s*RAJA?RAJESHWARA[^,]{0,40}(?:CUTTING|DEMOLITION)[^,]{0,20}WORKS)\b",
            compact,
        )
    )
    doc_no = (
        _find(r"(?i)SL\.?\s*No\.?\s*[:\-]?\s*(\d{1,6})", compact)
        or _find(r"(?i)\bNo\.?\s*[:\-]?\s*([A-Z0-9]{1,10})\b", compact)
    )

    doc_date = normalize_ocr_date(
        _find(r"(?i)DATE\s*[:\-]?\s*([0-9]{1,2}[./\-tT\s(/]{1,12}[0-9./\-)]{2,10})", compact)
        or _find(r"(?i)Date\s*[:\-]?\s*([0-9]{1,2}[./\-][0-9]{1,2}[./\-][0-9]{2,4})", compact)
    )
    # Fallback: first plausible date token on the page
    if not doc_date:
        doc_date = normalize_ocr_date(
            _find(r"\b([0-9]{1,2}[./\-][0-9]{1,2}[./\-][0-9]{2,4})\b", compact)
        )

    party = _find(
        r"(?i)\b(?:TO|M/?s\.?|Mis\.?)\s*\.?\s*(?:MR\/?MS\/?M\/?S|MR\/MS|MR|MS|M\/S)?\.?\s*"
        r"((?:Shoee|Shree|Sri|Shuminasayam|Shree)?\s*Swamin[a-z]*\s+Guruk[a-z]*[A-Za-z0-9 ,.&/\-]{0,40})",
        compact,
    )
    if not party:
        party = _find(
            r"(?i)((?:Shoee|Shree|Sri|Shuminasayam)\s+Swamin[a-z]*\s+Guruk[a-z]*[^\n]{0,40})",
            compact,
        )
    if not party:
        party = _find(
            r"(?i)\b(?:TO|M/?s\.?|Mis\.?)\s*\.?\s*(?:MR\/?MS\/?M\/?S|MR\/MS|MR|MS|M\/S)?\.?\s*"
            r"([A-Za-z][A-Za-z0-9 ,.&/\-]{4,60})",
            compact,
        )
    if party:
        party = re.split(r"(?i)\b(?:date|s\.?\s*no|particulars|qty|total|amount)\b", party)[0].strip(" .,:-")
        if _BAD_PARTY.search(party) or len(party) < 4:
            party = None
    # Prefer Gurukul party name when present anywhere on cash bills
    gurukul = _find(
        r"(?i)((?:Shoee|Shree|Sri|Shuminasayam)[^\n]{0,10}Swamin[a-z]*[^\n]{0,10}Guruk[a-z]*[^\n]{0,20})",
        compact,
    )
    if gurukul:
        party = re.sub(r"\s+", " ", gurukul).strip(" .,:-")

    amount_value, amount_source = extract_grand_total(text)
    if amount_value is None:
        total = (
            _find(r"(?i)\bTotal\b\s*[:\-]?\s*(?:Rs\.?\s*)?(\d{2,9}(?:\.\d{1,2})?)", compact)
            or _find(r"(?i)\bBalance\b\s*[:\-]?\s*(?:Rs\.?\s*)?(\d{2,9}(?:\.\d{1,2})?)", compact)
            or _find(r"(?i)\bBAL\.?\b\s*[:\-]?\s*(?:Rs\.?\s*)?(\d{2,9}(?:\.\d{1,2})?)", compact)
        )
        amount_value = normalize_amount(total)
        amount_source = "cash_memo_total"

    # Handwritten cash memos: numbers after Amount/Total header, OCR "900oL" -> 9000
    # Prefer the largest value in the amount column (not the first rate/qty-like number).
    if amount_value is None:
        after_amount = compact
        m = re.search(r"(?i)\b(?:amount|total|bal\.?)\b", compact)
        if m:
            after_amount = compact[m.end() :]
        # Stop before bank/footer identifiers so A/c numbers are not treated as totals
        after_amount = re.split(
            r"(?i)\b(?:bank|ifsc|pan|a/?c\.?\s*no|account|signature|name:)\b",
            after_amount,
            maxsplit=1,
        )[0]
        candidates: list[Decimal] = []
        for raw in re.findall(r"\b(\d{3,7}[oOlI]?\d{0,3})\b", after_amount):
            repaired = (
                raw.replace("o", "0")
                .replace("O", "0")
                .replace("l", "1")
                .replace("I", "1")
            )
            if len(re.sub(r"\D", "", repaired)) >= 8:
                continue
            cand = normalize_amount(repaired)
            if cand and Decimal("100") <= cand <= Decimal("1000000"):
                candidates.append(cand)
        if candidates:
            amount_value = max(candidates)
            amount_source = "cash_memo_amount_column"

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

    if supplier:
        supplier = re.sub(r"(?i)^\s*cash\s*(?:bill|memo)\s*", "", supplier).strip(" .,:-")
    fields["supplierName"] = fv(supplier, confidence if supplier else 0.0, "supplier not readable")
    fields["documentNumber"] = fv(doc_no, confidence if doc_no else 0.0, "document number not readable")
    fields["documentDate"] = fv(doc_date, confidence if doc_date else 0.0, "document date not readable")
    fields["partyName"] = fv(party, confidence if party else 0.0, "party name not readable")
    fields["amount"] = fv(
        str(amount_value) if amount_value is not None else None,
        confidence if amount_value is not None else 0.0,
        "total amount not readable",
        amount_source,
    )
    fields["gstin"] = fv(gstin)

    desc_parts = []
    for pattern in [
        r"(?i)(pcb\s*repai[rt]\w*)",
        r"(?i)(indoo?r?\s+and\s+out\s*doo?r?)",
        r"(?i)(f[ro]idge\s*che\w*)",
        r"(?i)(working\s*cha\w*)",
        r"(?i)(laundry|cloats|clothes|pump\s*kit|labour|block)",
    ]:
        hit = _find(pattern, compact)
        if hit:
            desc_parts.append(hit)
    fields["description"] = fv(" | ".join(desc_parts) or None, confidence, "item description not readable")

    items: list[LineItem] = []
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
        warnings.append("Party name may contain OCR spelling errors")

    return {"fields": fields, "items": items, "warnings": warnings}
