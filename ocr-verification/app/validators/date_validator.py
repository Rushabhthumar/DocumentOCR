from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

from app.models.verification_models import FieldComparisonResult


_DATE_PATTERNS = [
    ("%d-%m-%Y", re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b")),
    ("%d/%m/%Y", re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")),
    ("%d-%m-%y", re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{2})\b")),
    ("%d/%m/%y", re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2})\b")),
    ("%Y-%m-%d", re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")),
]


def _parse_with_format(text: str, fmt: str) -> Optional[date]:
    try:
        return datetime.strptime(text, fmt).date()
    except ValueError:
        return None


def normalize_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None

    # ISO datetime from finance API
    if "T" in text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            pass

    for fmt, pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        raw = match.group(0)
        parsed = _parse_with_format(raw, fmt)
        if parsed:
            return parsed
    return None


def compare_dates(
    expected,
    extracted,
    *,
    tolerance_days: int = 0,
    field_name: str = "date",
) -> FieldComparisonResult:
    exp = normalize_date(expected)
    ext = normalize_date(extracted)
    if exp is None or ext is None:
        return FieldComparisonResult(
            field_name=field_name,
            expected=exp.isoformat() if exp else None,
            extracted=ext.isoformat() if ext else None,
            matched=False,
            status="INSUFFICIENT_DATA",
            explanation="Date missing on transaction or document",
        )
    diff_days = abs((exp - ext).days)
    matched = diff_days <= tolerance_days
    return FieldComparisonResult(
        field_name=field_name,
        expected=exp.isoformat(),
        extracted=ext.isoformat(),
        matched=matched,
        difference=diff_days,
        status="MATCH" if matched else "MISMATCH",
        explanation=(
            f"Dates match within {tolerance_days} day(s)"
            if matched
            else f"Date difference is {diff_days} day(s)"
        ),
    )