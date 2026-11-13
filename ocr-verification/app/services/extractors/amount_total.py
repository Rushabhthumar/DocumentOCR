from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

from app.validators.amount_validator import normalize_amount

# Indian (2,80,768.00), Western (280,768.00), or plain (280768.00)
_AMOUNT = (
    r"("
    r"\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d{1,2})?"
    r"|"
    r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"
    r"|"
    r"\d+(?:\.\d{1,2})?"
    r")"
)

# Highest-priority payable / invoice totals only (never line qty / tax lines)
_GRAND_TOTAL_PATTERNS = [
    rf"(?i)grand\s*total\s*[:\-]?\s*(?:rs\.?\s*|inr\s*|₹\s*)?{_AMOUNT}",
    rf"(?i)total\s*inv\.?\s*amt\.?\s*[:\-]?\s*(?:rs\.?\s*|inr\s*|₹\s*)?{_AMOUNT}",
    rf"(?i)total\s*invoice\s*amt\.?\s*[:\-]?\s*(?:rs\.?\s*|inr\s*|₹\s*)?{_AMOUNT}",
    rf"(?i)invoice\s*amount\s*[:\-]?\s*(?:rs\.?\s*|inr\s*|₹\s*)?{_AMOUNT}",
    rf"(?i)total\s*amount\s*after\s*tax\s*[:\-]?\s*(?:rs\.?\s*|inr\s*|₹\s*)?{_AMOUNT}",
    rf"(?i)(?:net\s*payable|amount\s*payable|amount\s*due|bill\s*amount)\s*[:\-]?\s*(?:rs\.?\s*|inr\s*|₹\s*)?{_AMOUNT}",
    rf"(?i)\bbalance\b\s*[:\-]?\s*(?:rs\.?\s*|inr\s*|₹\s*)?{_AMOUNT}",
    rf"(?i)\btotal\b\s*[:\-]?\s*(?:rs\.?\s*|inr\s*|₹\s*)?{_AMOUNT}",
]

_REJECT_NEAR = re.compile(
    r"(?i)(?:cgst|sgst|igst|cess|taxable|sub\s*total|quantity|qty|sqf|sq\.?\s*ft|"
    r"rate\s*%|hsn|copies|reading)",
)


def extract_grand_total(text: str) -> tuple[Optional[Decimal], Optional[str]]:
    """
    Return (amount, source_label) for the document's payable grand total only.
    Does not sum multiple pages/labels — caller dedupes across pages.
    """
    if not text:
        return None, None
    # Keep newlines as spaces so label and value on adjacent OCR lines still match
    compact = re.sub(r"[ \t]+", " ", text)
    compact = re.sub(r"\s*:\s*", " : ", compact)

    best: Optional[Decimal] = None
    best_source: Optional[str] = None
    best_rank = 999

    for rank, pattern in enumerate(_GRAND_TOTAL_PATTERNS):
        for match in re.finditer(pattern, compact):
            raw = match.group(1)
            # Weak bare "Total" often sits near tax/qty lines — skip those
            prefix = compact[max(0, match.start() - 40) : match.start()]
            if rank >= 7 and _REJECT_NEAR.search(prefix):
                continue
            amount = normalize_amount(raw)
            if amount is None or amount < Decimal("1.00"):
                continue
            # Quantity-like values next to SQF etc.
            window = compact[match.start() : min(len(compact), match.end() + 12)]
            if re.search(r"(?i)\b(?:sqf|sq\.?\s*ft|qty|nos?)\b", window):
                continue
            if best is None or rank < best_rank:
                best = amount
                best_rank = rank
                best_source = pattern
            elif rank == best_rank and amount > best:
                # Same label strength: prefer the larger payable (not a partial)
                best = amount
                best_source = pattern
        if best is not None and best_rank <= 3:
            # Strong label found — stop searching weaker labels
            break

    return best, best_source


def amounts_equivalent(a: Decimal, b: Decimal, tolerance: Decimal = Decimal("1.00")) -> bool:
    return abs(a - b) <= tolerance
