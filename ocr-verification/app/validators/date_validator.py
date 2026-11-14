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
    # Mixed separators common in handwriting OCR: 01-4.26 / 01-4/26 / 6.4-26
    ("%d-%m-%y", re.compile(r"\b(\d{1,2})-(\d{1,2})[./](\d{2})\b")),
    ("%d.%m-%y", re.compile(r"\b(\d{1,2})\.(\d{1,2})[/-](\d{2})\b")),
    ("%d/%m-%y", re.compile(r"\b(\d{1,2})/(\d{1,2})[.\-](\d{2})\b")),
    ("%d-%m-%Y", re.compile(r"\b(\d{1,2})-(\d{1,2})[./](\d{4})\b")),
    ("%Y-%m-%d", re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")),
    # Printed Indian invoices: 6-Apr-26 / 06-Apr-2026
    ("%d-%b-%y", re.compile(r"\b(\d{1,2})[-/\s]+([A-Za-z]{3,9})[-/\s]+(\d{2})\b")),
    ("%d-%b-%Y", re.compile(r"\b(\d{1,2})[-/\s]+([A-Za-z]{3,9})[-/\s]+(\d{4})\b")),
    ("%d-%B-%y", re.compile(r"\b(\d{1,2})[-/\s]+([A-Za-z]{3,9})[-/\s]+(\d{2})\b")),
    ("%d-%B-%Y", re.compile(r"\b(\d{1,2})[-/\s]+([A-Za-z]{3,9})[-/\s]+(\d{4})\b")),
]


def _parse_with_format(text: str, fmt: str) -> Optional[date]:
    try:
        if any(token in fmt for token in ("%b", "%B")):
            match = re.search(
                r"(?i)(\d{1,2})[-/\s]+([A-Za-z]{3,9})[-/\s]+(\d{2,4})",
                text.strip(),
            )
            if not match:
                return None
            day, mon, year = match.group(1), match.group(2)[:3].title(), match.group(3)
            text = f"{day}-{mon}-{year}"
            fmt = "%d-%b-%Y" if len(year) == 4 else "%d-%b-%y"
        elif fmt.count("%") == 3 and "-" in fmt:
            # Rebuild from regex groups already captured as full match with mixed seps
            parts = re.split(r"[./\-]", text.strip())
            if len(parts) == 3 and fmt.startswith("%d"):
                text = f"{parts[0]}-{parts[1]}-{parts[2]}"
                if len(parts[2]) == 4:
                    fmt = "%d-%m-%Y"
                else:
                    fmt = "%d-%m-%y"
        return datetime.strptime(text, fmt).date()
    except ValueError:
        return None


def _unglue_date_digits(digits: str) -> Optional[str]:
    """Turn OCR-glued dates like 18426 / 020626 / Date.184126 into dd/mm/yy."""
    if not digits or not digits.isdigit():
        return None

    parsed: list[tuple[int, str]] = []

    def _consider(d: int, m: int, y: int, score: int) -> None:
        if 1 <= d <= 31 and 1 <= m <= 12 and 0 <= y <= 99:
            parsed.append((score, f"{d:02d}/{m:02d}/{y:02d}"))

    # Exact lengths first
    if len(digits) == 6:
        _consider(int(digits[:2]), int(digits[2:4]), int(digits[4:]), 0)
    if len(digits) == 5:
        _consider(int(digits[:2]), int(digits[2:3]), int(digits[3:]), 1)
        _consider(int(digits[:1]), int(digits[1:3]), int(digits[3:]), 2)

    # One extra OCR digit (slash/noise): try day + (month/year with one deletion)
    if len(digits) in {6, 7}:
        for day_len in (2, 1):
            d = int(digits[:day_len])
            rest = digits[day_len:]
            for i, ch in enumerate(rest):
                trimmed = rest[:i] + rest[i + 1 :]
                # Prefer deleting separator-like noise ('1' from '/' or dotted junk)
                noise_bonus = -1 if ch in {"1"} else 0
                if len(trimmed) == 4:
                    _consider(d, int(trimmed[:2]), int(trimmed[2:]), 3 + noise_bonus)
                if len(trimmed) == 3:
                    _consider(d, int(trimmed[:1]), int(trimmed[1:]), 4 + noise_bonus)

    if not parsed:
        return None
    # Prefer recent years (20-35), then lower score
    def _key(row: tuple[int, str]) -> tuple[int, int]:
        _score, value = row
        y = int(value.split("/")[-1])
        year_pen = 0 if 20 <= y <= 35 else 5
        return (year_pen, _score)

    parsed.sort(key=_key)
    return parsed[0][1]


def repair_ocr_date_text(text: str) -> str:
    """Fix common OCR date glitches before parsing."""
    cleaned = text.strip()
    cleaned = re.sub(
        r"(?i)^(?:invoice|bill|voucher|receipt|challan|document|payment)?\s*date\s*[:\-]?\s*",
        "",
        cleaned,
    )
    cleaned = re.sub(r"(?i)^dated\s*[:\-]?\s*", "", cleaned).strip()
    # Date.184126 / Date. 0.20626 → unglue digit cluster near Date label
    glued = re.search(
        r"(?i)\b(?:date|dated)\s*[.:\-]?\s*([0-9][0-9./\-\s]{3,12})",
        text,
    )
    if glued:
        digits = re.sub(r"\D", "", glued.group(1))
        unglued = _unglue_date_digits(digits)
        if unglued:
            cleaned = unglued
            return cleaned
    # Bare glued cluster only when the string is almost purely a digit date
    bare_digits = re.sub(r"\D", "", cleaned)
    non_digit_noise = re.sub(r"[\d\s./\-]", "", cleaned)
    if (
        len(bare_digits) in {5, 6}
        and len(non_digit_noise) <= 1
        and not re.search(r"\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}", cleaned)
    ):
        unglued = _unglue_date_digits(bare_digits)
        if unglued:
            return unglued
    # 08/11/2.6 -> 08/11/26 ; 07 t04/2026 -> 07/04/2026
    cleaned = re.sub(r"(\d{1,2}[./\-]\d{1,2}[./\-])(\d)\.(\d)\b", r"\1\2\3", cleaned)
    cleaned = re.sub(
        r"\b(\d{1,2})\s*[tT]\s*(\d{1,2})([./\-])(\d{2,4})\b",
        r"\1/\2\3\4",
        cleaned,
    )
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
