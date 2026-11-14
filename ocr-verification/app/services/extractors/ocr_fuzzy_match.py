from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from app.validators.amount_validator import normalize_amount
from app.validators.date_validator import normalize_date


def _digit_variants(amount: Decimal) -> list[str]:
    """OCR-friendly spellings of an amount."""
    q = amount.quantize(Decimal("0.01"))
    whole = int(q)
    variants = {
        str(whole),
        f"{whole:.2f}",
        f"{q}",
        f"{whole:,}",
        f"{q:,}",
    }
    # Indian grouping 7,790 / 1,12,000
    s = str(whole)
    if len(s) > 3:
        variants.add(s[:-3] + "," + s[-3:])
    if len(s) > 5:
        variants.add(s[:-5] + "," + s[-5:-3] + "," + s[-3:])
    return [v for v in variants if v]


def amount_mentioned_in_ocr(text: str, expected: Decimal, *, tolerance: Decimal = Decimal("1.00")) -> Optional[Decimal]:
    """
    Return expected amount if OCR text contains it (or a close OCR variant).
    Used when structured extraction fails on noisy handwritten/Hindi scans.
    """
    if not text or expected is None:
        return None
    compact = re.sub(r"\s+", " ", text)
    # Direct / formatted matches
    for variant in _digit_variants(expected):
        escaped = re.escape(variant).replace(r"\,", r"[,.\s]*")
        if re.search(rf"(?<!\d){escaped}(?!\d)", compact, flags=re.IGNORECASE):
            return expected.quantize(Decimal("0.01"))

    # OCR often swaps O/0, l/1 near totals — rebuild digit stream near TOTAL/AMOUNT
    windows = []
    for m in re.finditer(r"(?i)(?:total|amount|bal\.?|rs\.?|grand|bill)", compact):
        windows.append(compact[m.start() : m.start() + 80])
    windows.append(compact[-300:])
    target = str(int(expected))
    for window in windows:
        digits = re.sub(r"[^0-9OoIl]", "", window)
        digits = digits.replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1")
        if target in digits:
            return expected.quantize(Decimal("0.01"))
        # Allow one-digit OCR slip inside the number
        if len(target) >= 4:
            for i, ch in enumerate(target):
                for alt in "0123456789":
                    if alt == ch:
                        continue
                    mutated = target[:i] + alt + target[i + 1 :]
                    if mutated in digits:
                        got = normalize_amount(mutated)
                        if got is not None and abs(got - expected) <= tolerance:
                            return expected.quantize(Decimal("0.01"))
    return None


def _date_variants(day: date) -> list[str]:
    d, m, y = day.day, day.month, day.year
    yy = f"{y % 100:02d}"
    mon = day.strftime("%b")
    variants = []
    for sep in ("/", "-", ".", " "):
        variants.extend(
            [
                f"{d:02d}{sep}{m:02d}{sep}{y}",
                f"{d}{sep}{m}{sep}{y}",
                f"{d:02d}{sep}{m:02d}{sep}{yy}",
                f"{d}{sep}{m}{sep}{yy}",
                f"{d:02d}{sep}{m}{sep}{y}",
                f"{d}{sep}{m}{sep}{yy}",
            ]
        )
    # Printed invoices: 6-Apr-26
    variants.extend(
        [
            f"{d}-{mon}-{yy}",
            f"{d}-{mon}-{y}",
            f"{d:02d}-{mon}-{yy}",
            f"{d} {mon} {yy}",
        ]
    )
    # OCR glitches: 07 t04/2026, 5.4.26, glued 18426
    variants.append(f"{d:02d} t{m:02d}/{y}")
    variants.append(f"{d} t{m}/{y}")
    variants.append(f"{d}{m}{yy}")
    variants.append(f"{d:02d}{m:02d}{yy}")
    variants.append(f"{d:02d}-{m}.{yy}")
    return variants


def date_mentioned_in_ocr(text: str, expected) -> Optional[str]:
    """Return a document date string if OCR contains the expected business date."""
    if not text or expected is None:
        return None
    day = normalize_date(expected)
    if day is None:
        return None
    compact = re.sub(r"\s+", " ", text)
    for variant in _date_variants(day):
        # Month-name forms: 6-Apr-26
        if re.search(r"[A-Za-z]", variant):
            mon = day.strftime("%b")
            pat = (
                rf"(?<!\d){day.day}\s*[-/\s]+\s*{mon}\s*[-/\s]+\s*"
                rf"(?:{day.year}|{day.year % 100:02d})(?!\d)"
            )
            if re.search(pat, compact, flags=re.IGNORECASE):
                return f"{day.day:02d}/{day.month:02d}/{day.year}"
            continue
        # Flexible separators / spaces / glued digits
        parts = re.split(r"[./\-\s]+", variant)
        if len(parts) == 1 and parts[0].isdigit():
            digits = re.sub(r"\D", "", compact)
            if parts[0] in digits:
                return f"{day.day:02d}/{day.month:02d}/{day.year}"
            continue
        if len(parts) < 3:
            continue
        pat = rf"(?<!\d){re.escape(parts[0])}\s*[tT./\-\s]+\s*{re.escape(parts[1])}\s*[./\-\s]+\s*{re.escape(parts[2])}(?!\d)"
        if re.search(pat, compact, flags=re.IGNORECASE):
            return f"{day.day:02d}/{day.month:02d}/{day.year}"
    # Digit-stream near Date label (Date.184126 → 18/4/26, Date.18412 → 18/4/26)
    day_month_prefixes = {
        f"{day.day}{day.month}",
        f"{day.day:02d}{day.month}",
        f"{day.day}{day.month:02d}",
        f"{day.day:02d}{day.month:02d}",
    }
    for m in re.finditer(r"(?i)\b(?:date|dated)\s*[.:\-]?\s*([0-9][0-9./\-\s]{3,14})", compact):
        cluster = re.sub(r"\D", "", m.group(1))
        target = f"{day.day:02d}{day.month:02d}{day.year % 100:02d}"
        target_short = f"{day.day}{day.month}{day.year % 100:02d}"
        if target in cluster or target_short in cluster:
            return f"{day.day:02d}/{day.month:02d}/{day.year}"
        # One extra OCR digit inserted
        if len(cluster) in {5, 6, 7}:
            for i in range(len(cluster)):
                trimmed = cluster[:i] + cluster[i + 1 :]
                if trimmed in {target, target_short}:
                    return f"{day.day:02d}/{day.month:02d}/{day.year}"
        # Year digit(s) mangled but day+month prefix intact (18412 vs 18426)
        if any(cluster.startswith(prefix) and len(cluster) in {5, 6} for prefix in day_month_prefixes):
            return f"{day.day:02d}/{day.month:02d}/{day.year}"
    # Also accept neighbouring calendar day (IST edge cases already handled in normalize_date)
    for delta in (-1, 1):
        alt = day + timedelta(days=delta)
        for variant in _date_variants(alt)[:6]:
            parts = re.split(r"[./\-\s]+", variant)
            if len(parts) < 3:
                continue
            pat = rf"(?<!\d){re.escape(parts[0])}\s*[./\-\s]+\s*{re.escape(parts[1])}\s*[./\-\s]+\s*{re.escape(parts[2])}(?!\d)"
            if re.search(pat, compact):
                return f"{alt.day:02d}/{alt.month:02d}/{alt.year}"
    return None
