from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

from app.models.extraction_models import FieldValue
from app.services.extractors.amount_total import extract_grand_total, looks_like_phone_or_id
from app.services.extractors.ocr_date import normalize_ocr_date
from app.validators.amount_validator import normalize_amount


_DATE_TOKEN = re.compile(
    r"\b(\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}"
    r"|\d{1,2}[-/\s]+[A-Za-z]{3,9}['’]?[-/\s]*\d{2,4})\b"
)
_AMOUNT_TOKEN = re.compile(
    r"(?<!\d)((?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d{1,2})?)(?!\d)"
)

_AMOUNT_CAPTURE = r"((?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d{1,2})?)"
_MONTH_DATE = (
    r"(\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}"
    r"|\d{1,2}[-/\s]+[A-Za-z]{3,9}['’]?[-/\s]*\d{2,4})"
)

# Prefer payable / final totals over line amounts
_AMOUNT_LABEL_PATTERNS = [
    (
        r"(?i)(?:grand\s*total|net\s*payable|amount\s*payable|total\s*amount\s*after\s*tax|"
        r"total\s*amount\s*due|bill\s*amount|invoice\s*amount|net\s*amount|"
        r"transaction\s*amount|total\s*due|amount\s*in\s*figures|total\s*value|"
        r"total\s*bill\s*amount)\s*[:\-]?\s*"
        r"(?:rs\.?\s*|inr\s*|₹\s*)?" + _AMOUNT_CAPTURE
    ),
    (
        r"(?i)(?:total\s*amount|total\s*amt|net\s*total|final\s*total|total)\s*[:\-]?\s*"
        r"(?:rs\.?\s*|inr\s*|₹\s*)?" + _AMOUNT_CAPTURE
    ),
    r"(?i)(?:balance|amount)\s*[:\-]?\s*(?:rs\.?\s*|inr\s*|₹\s*)?" + _AMOUNT_CAPTURE,
]

_DATE_LABEL_PATTERNS = [
    # UPI / bank PDF receipts — "Generated on" is the payment business date
    rf"(?i)generated\s*on\s*[:\-]?\s*{_MONTH_DATE}",
    (
        r"(?i)l?nvoice\s*date\s*[:\-]?\s*"
        r"(\d{1,2}\s*[tT./\-]\s*\d{1,2}[./\-]\d{2,4})"
    ),
    (
        r"(?i)(?:bill\s*date|voucher\s*date|receipt\s*date|"
        r"challan\s*date|document\s*date|txn\s*date|transaction\s*date|"
        r"payment\s*date|pymt_date|dated)\s*[:\-]?\s*" + _MONTH_DATE
    ),
    # Bare Date — but skip when the label is Due Date (handled by negative lookbehind)
    r"(?i)(?<!due\s)\bdate\s*[:\-]?\s*" + _MONTH_DATE,
]

_DOC_NO_PATTERNS = [
    r"(?i)(?:invoice\s*no\.?|inv\.?\s*no\.?|bill\s*no\.?|voucher\s*no\.?|"
    r"receipt\s*no\.?|challan\s*no\.?|document\s*no\.?|ref\.?\s*no\.?|"
    r"sl\.?\s*no\.?)\s*[:\-]?\s*([A-Z0-9][A-Z0-9/_\-]{1,24})",
]

_PARTY_PATTERNS = [
    r"(?i)(?:billed\s*to|bill\s*to|sold\s*to|ship\s*to|customer\s*name|"
    r"party\s*name|supplier\s*name|vendor\s*name|received\s*from|"
    r"paid\s*to|m/?s\.?)\s*[:\-]?\s*([A-Za-z][A-Za-z0-9 .,&'\-]{3,80})",
    r"(?i)\bto\.?\s*(?:mr\/?ms\/?m\/?s|mr\/ms|mr|ms|m\/s)?\.?\s*"
    r"([A-Za-z][A-Za-z0-9 .,&'\-]{3,80})",
    r"(?i)(?:shree|sri|shoee)\s+swamin[a-z]*\s+guruk[a-z]*[^\n]{0,40}",
]


def _find(pattern: str, text: str) -> Optional[str]:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip() if match.lastindex else match.group(0).strip()


def _pick_labeled_amount(compact: str) -> Optional[Decimal]:
    for pattern in _AMOUNT_LABEL_PATTERNS:
        for match in re.finditer(pattern, compact):
            amount = normalize_amount(match.group(1))
            if amount is None:
                continue
            # Ignore tiny / reading-meter style noise
            if amount < Decimal("1.00"):
                continue
            return amount
    return None


def _pick_fallback_amount(compact: str) -> Optional[Decimal]:
    """
    Prefer larger currency-like amounts near the end of the document
    (typical for totals), skipping obvious meter/account numbers.
    """
    candidates: list[tuple[int, Decimal]] = []
    for match in _AMOUNT_TOKEN.finditer(compact):
        raw = match.group(1)
        prefix = compact[max(0, match.start() - 30) : match.start()]
        # Skip bank / tax / phone identifiers mistaken as totals
        if re.search(
            r"(?i)(?:a/?c\.?\s*no|account|ifsc|pan|gstin|gstn|cell|phone|mob|pin|"
            r"hsn|sac|invoice\s*no|grn\s*no|gate\s*entry)",
            prefix,
        ):
            continue
        # Skip long digit runs without decimals (account / meter numbers)
        digits = raw.replace(",", "").replace(".", "")
        if "." not in raw and len(digits) >= 8:
            continue
        amount = normalize_amount(raw)
        # Ignore tiny OCR noise (14, 11) — bill totals in this domain are almost never < 100
        if amount is None or amount < Decimal("100.00"):
            continue
        if amount > Decimal("100000000"):
            continue
        if looks_like_phone_or_id(amount, compact):
            continue
        # Qty-like decimals (5104.87 Sqf) — skip when unit words follow
        window = compact[match.start() : min(len(compact), match.end() + 12)]
        if re.search(r"(?i)\b(?:sqf|sq\.?\s*ft|tons?|qty|nos?)\b", window):
            continue
        candidates.append((match.start(), amount))
    if not candidates:
        return None
    # Prefer amounts in the last 40% of the text (totals usually at bottom)
    cutoff = int(len(compact) * 0.6)
    bottom = [a for pos, a in candidates if pos >= cutoff]
    pool = bottom or [a for _, a in candidates]
    # Among remaining, prefer the largest plausible total (typical bill total)
    return max(pool)


def extract_generic_fields(text: str, page_number: int = 1, confidence: float = 0.0) -> dict:
    """
    Document-agnostic extraction of amount / date / party / document number.
    Used for every page as a fill-in fallback so unknown layouts still verify.
    """
    warnings: list[str] = []
    fields: dict[str, FieldValue] = {}
    compact = re.sub(r"\s+", " ", text or "")

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

    amount, amount_src = extract_grand_total(text)
    amount_source = amount_src or "grand_total"
    if amount is None:
        amount = _pick_labeled_amount(compact)
        amount_source = "labeled_total"
    if amount is None:
        amount = _pick_fallback_amount(compact)
        amount_source = "heuristic_total"
        if amount is not None:
            warnings.append(f"Amount inferred heuristically as {amount}")

    doc_date = None
    for pattern in _DATE_LABEL_PATTERNS:
        doc_date = normalize_ocr_date(_find(pattern, compact))
        if doc_date:
            break
    if not doc_date:
        # First plausible date token in the document
        match = _DATE_TOKEN.search(compact)
        if match:
            doc_date = normalize_ocr_date(match.group(1))
            warnings.append(f"Date inferred from first date token: {doc_date}")

    doc_no = None
    for pattern in _DOC_NO_PATTERNS:
        doc_no = _find(pattern, compact)
        if not doc_no:
            continue
        # Skip pure dates captured as document numbers
        from app.validators.date_validator import normalize_date

        if normalize_date(doc_no) is not None and not re.search(r"[A-Za-z]", doc_no):
            doc_no = None
            continue
        break

    party = None
    for pattern in _PARTY_PATTERNS:
        party = _find(pattern, compact)
        if not party:
            continue
        party = re.sub(r"\s+", " ", party).strip(" .,:-")
        party = re.sub(r"(?i)^HSN\s*&?\s*SAC\s*Code\s*[:\-]?\s*[0-9A-Z]+\s*", "", party).strip()
        if re.search(r"(?i)(?:hsn|sac\s*code|impeccable|particulars|qty|amount)", party):
            party = None
            continue
        break

    description = None
    for pattern in [
        r"(?i)particulars?\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9 ,./\-]{5,100})",
        r"(?i)(?:narration|description|remarks?)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9 ,./\-]{5,100})",
    ]:
        description = _find(pattern, compact)
        if description:
            break

    fields["amount"] = fv(
        str(amount) if amount is not None else None,
        confidence if amount is not None else 0.0,
        "amount not found",
        amount_source if amount is not None else None,
    )
    fields["documentDate"] = fv(
        doc_date, confidence if doc_date else 0.0, "date not found", "generic_date"
    )
    fields["documentNumber"] = fv(
        doc_no, confidence if doc_no else 0.0, "document number not found", "generic_doc_no"
    )
    fields["partyName"] = fv(
        party, confidence if party else 0.0, "party not found", "generic_party"
    )
    fields["description"] = fv(
        description, confidence if description else 0.0, "description not found"
    )

    if amount is None:
        warnings.append("Generic extractor could not find an amount")
    if doc_date is None:
        warnings.append("Generic extractor could not find a date")

    return {"fields": fields, "items": [], "warnings": warnings}
