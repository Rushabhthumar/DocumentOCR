from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.models.verification_models import FieldComparisonResult


_DATE_PATTERNS = [
    ("%d-%m-%Y", re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b")),
    ("%d/%m/%Y", re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")),
    ("%d.%m.%Y", re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")),
    ("%d-%m-%y", re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{2})\b")),
    ("%d/%m/%y", re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2})\b")),
    ("%d.%m.%y", re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2})\b")),
    ("%Y-%m-%d", re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")),
]


def _parse_with_format(text: str, fmt: str) -> Optional[date]:
    try:
        return datetime.strptime(text, fmt).date()
    except ValueError:
        return None


def repair_ocr_date_text(text: str) -> str:
    """Fix common OCR date glitches before parsing."""
    cleaned = text.strip()
    cleaned = re.sub(
        r"(?i)^(?:invoice|bill|voucher|receipt|challan|document|payment)?\s*date\s*[:\-]?\s*",
        "",
        cleaned,
    )
    cleaned = re.sub(r"(?i)^dated\s*[:\-]?\s*", "", cleaned).strip()
    # 08/11/2.6 -> 08/11/26
    cleaned = re.sub(r"(\d{1,2}[./\-]\d{1,2}[./\-])(\d)\.(\d)\b", r"\1\2\3", cleaned)
    # 31/10/226 -> 31/10/2026 (year lost one digit of 20xx)
    cleaned = re.sub(
        r"\b(\d{1,2})([./\-])(\d{1,2})\2(2\d{2})\b",
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(2)}20{m.group(4)[1:]}",
        cleaned,
    )
    # "12 t 11/2026" — day + month + year with OCR junk between
    messy = re.search(
        r"\b(\d{1,2})\s*[tT]?\s*[./\-(\s]+(\d{1,2})\s*[./\-)]+\s*(20\d{2}|\d{2})\b",
        cleaned,
    )
    if messy and not re.search(r"\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}", cleaned):
        cleaned = f"{messy.group(1)}/{messy.group(2)}/{messy.group(3)}"
    # "12 t (/2026" — month unreadable; do not invent a date (avoid 12/20/26)
    # Missing separator before short year ONLY when not already a real year: 08/1126 -> 08/11/26
    cleaned = re.sub(
        r"\b(\d{1,2})([./\-])(\d{4})\b",
        lambda m: m.group(0)
        if m.group(3).startswith(("19", "20"))
        else f"{m.group(1)}{m.group(2)}{m.group(3)[:2]}{m.group(2)}{m.group(3)[2:]}",
        cleaned,
    )
    return cleaned


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

    # ISO datetime from finance API — use India business date (IST), not UTC calendar day
    if "T" in text:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return dt.date()
            ist = timezone(timedelta(hours=5, minutes=30))
            return dt.astimezone(ist).date()
        except ValueError:
            pass

    text = repair_ocr_date_text(text)
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
