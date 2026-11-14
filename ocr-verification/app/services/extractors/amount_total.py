from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

from app.services.extractors.amount_words import extract_amount_from_words
from app.validators.amount_validator import normalize_amount

# Indian (2,80,768.00), Western (280,768.00), plain (280768.00), cash-memo 3900=00
_AMOUNT = (
    r"("
    r"\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d{1,2})?"
    r"|"
    r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"
    r"|"
    r"\d+(?:\.\d{1,2})?"
    r"|"
    r"\d+\s*=\s*0{1,2}"
    r")"
)

# Highest-priority payable / invoice totals only (never line qty / tax lines)
# Allow a little OCR junk between the label and digits (TOTAL # 7790, TOTAL ∞ 13465)
_GAP = r"[^\d]{0,24}"
_GRAND_TOTAL_PATTERNS = [
    rf"(?i)grand\s*total{_GAP}(?:rs\.?\s*|inr\s*|₹\s*)?{_AMOUNT}",
    rf"(?i)total\s*inv\.?\s*amt\.?{_GAP}(?:rs\.?\s*|inr\s*|₹\s*)?{_AMOUNT}",
    rf"(?i)total\s*invoice\s*amt\.?{_GAP}(?:rs\.?\s*|inr\s*|₹\s*)?{_AMOUNT}",
    rf"(?i)invoice\s*amount{_GAP}(?:rs\.?\s*|inr\s*|₹\s*)?{_AMOUNT}",
    rf"(?i)total\s*amount\s*after\s*tax{_GAP}(?:rs\.?\s*|inr\s*|₹\s*)?{_AMOUNT}",
    rf"(?i)total\s*bill\s*amount{_GAP}(?:rs\.?\s*|inr\s*|₹\s*)?{_AMOUNT}",
    rf"(?i)(?:net\s*payable|amount\s*payable|amount\s*due|bill\s*amount){_GAP}(?:rs\.?\s*|inr\s*|₹\s*)?{_AMOUNT}",
    rf"(?i)(?:total\s*tai\s*amount|total\s*tax\s*amount|total\s*amt){_GAP}(?:rs\.?\s*|inr\s*|₹\s*)?{_AMOUNT}",
    rf"(?i)\bbalance\b{_GAP}(?:rs\.?\s*|inr\s*|₹\s*)?{_AMOUNT}",
    rf"(?i)\btotal\b{_GAP}(?:rs\.?\s*|inr\s*|₹\s*)?{_AMOUNT}",
    # Handwritten cash bills often put digits above the TOTAL label: 13465 / TOTAL
    rf"(?i){_AMOUNT}\s*(?:/-|=0{{0,2}})?\s*\btotal\b",
]

_REJECT_NEAR = re.compile(
    r"(?i)(?:cgst|sgst|igst|cess|taxable|sub\s*total|quantity|qty|sqf|sq\.?\s*ft|"
    r"rate\s*%|hsn|copies|reading)",
)


def amounts_equivalent(a: Decimal, b: Decimal, tolerance: Decimal = Decimal("1.00")) -> bool:
    return abs(a - b) <= tolerance


def _looks_like_phone_or_id(amount: Decimal, text: str = "") -> bool:
    """Reject Indian mobiles / account fragments mistaken for bill totals."""
    whole = int(amount)
    digits = str(whole)
    if len(digits) == 10 and digits[0] in "6789":
        return True
    # Truncated mobile fragments (911376 from 8770911376, 1519335 from 9301519335)
    if len(digits) >= 6 and amount == whole:
        for phone in re.findall(r"\b[6-9]\d{9}\b", text or ""):
            if digits in phone:
                return True
        if len(digits) >= 6 and digits[0] in "6789" and whole >= 100_000:
            return True
    return False


def extract_grand_total(text: str) -> tuple[Optional[Decimal], Optional[str]]:
    """
    Return (amount, source_label) for the document's payable grand total only.
    Prefers labeled numeric totals; reconciles with amount-in-words when OCR disagrees.
    """
    if not text:
        return None, None
    # Collapse all whitespace so "Grand Total" and value on next OCR line still match
    compact = re.sub(r"\s+", " ", text)
    compact = re.sub(r"\s*:\s*", " : ", compact)

    best: Optional[Decimal] = None
    best_source: Optional[str] = None
    best_rank = 999

    for rank, pattern in enumerate(_GRAND_TOTAL_PATTERNS):
        for match in re.finditer(pattern, compact):
            raw = match.group(1)
            prefix = compact[max(0, match.start() - 40) : match.start()]
            # Bare "Total <amount>" is polluted by tax/qty lines; amount-before-TOTAL
            # is anchored by the TOTAL label so table headers in the crop are OK.
            if rank == 9 and _REJECT_NEAR.search(prefix):
                continue
            if rank > 10 and _REJECT_NEAR.search(prefix):
                continue
            # Skip phone numbers sitting next to Mob./TOTAL crops
            near = compact[max(0, match.start() - 30) : min(len(compact), match.end() + 20)]
            if re.search(r"(?i)\b(?:mob\.?|phone|tel\.?|whats?app)\b", near):
                continue
            amount = normalize_amount(raw)
            if amount is None or amount < Decimal("1.00"):
                continue
            if _looks_like_phone_or_id(amount, compact):
                continue
            window = compact[match.start() : min(len(compact), match.end() + 12)]
            if re.search(r"(?i)\b(?:sqf|sq\.?\s*ft|qty|nos?)\b", window):
                continue
            if best is None or rank < best_rank:
                best = amount
                best_rank = rank
                best_source = pattern
            elif rank == best_rank and amount > best:
                best = amount
                best_source = pattern
        if best is not None and best_rank <= 3:
            break
        # Prefer Balance/payable over a later bare Total when both matched
        if best is not None and best_rank <= 8:
            break

    words_amount = extract_amount_from_words(text)
    if words_amount is not None:
        if best is None:
            return words_amount, "amount_in_words"
        # Cash-bill OCR often misreads TOTAL digits; words are more reliable when they diverge
        if not amounts_equivalent(best, words_amount, tolerance=Decimal("2.00")):
            # Prefer words when labeled total looks truncated / off by thousands
            if words_amount > best or abs(words_amount - best) >= Decimal("100"):
                return words_amount, "amount_in_words_preferred"
        elif amounts_equivalent(best, words_amount, tolerance=Decimal("2.00")):
            # Prefer exact words when within Rs 2 (round-off / OCR digit slip)
            if abs(words_amount - best) > 0 and words_amount >= best:
                return words_amount, "amount_in_words_reconciled"

    return best, best_source
