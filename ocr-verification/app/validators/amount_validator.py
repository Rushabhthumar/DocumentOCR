from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional

from app.models.verification_models import FieldComparisonResult


_CURRENCY_RE = re.compile(r"[₹$€£,\s]")
_AMOUNT_TOKEN_RE = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{2,3})*(?:\.\d+)?|\d+(?:\.\d+)?)(?!\d)")


def normalize_amount(value) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Rs.", "").replace("Rs", "").replace("INR", "")
    # Handwritten cash memos: 3900=00 / 3900=0 / 2600=00
    text = re.sub(r"(\d)\s*=\s*0{1,2}\b", r"\1.00", text)
    text = _CURRENCY_RE.sub("", text)
    text = text.replace("/-", "")
    try:
        return Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        match = _AMOUNT_TOKEN_RE.search(text) or _AMOUNT_TOKEN_RE.search(str(value))
        if not match:
            return None
        try:
            return Decimal(match.group(1).replace(",", "")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        except InvalidOperation:
            return None


def compare_amounts(
    expected,
    extracted,
    *,
    tolerance: Decimal = Decimal("0.01"),
    field_name: str = "amount",
) -> FieldComparisonResult:
    exp = normalize_amount(expected)
    ext = normalize_amount(extracted)
    if exp is None or ext is None:
        return FieldComparisonResult(
            field_name=field_name,
            expected=str(exp) if exp is not None else None,
            extracted=str(ext) if ext is not None else None,
            matched=False,
            status="INSUFFICIENT_DATA",
            explanation="Amount missing on transaction or document",
        )
    diff = abs(exp - ext)
    matched = diff <= tolerance
    return FieldComparisonResult(
        field_name=field_name,
        expected=str(exp),
        extracted=str(ext),
        matched=matched,
        difference=str(diff),
        status="MATCH" if matched else "MISMATCH",
        explanation=(
            f"Amounts match within tolerance {tolerance}"
            if matched
            else f"Amount difference {diff} exceeds tolerance {tolerance}"
        ),
    )