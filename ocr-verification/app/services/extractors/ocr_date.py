from __future__ import annotations

import re
from typing import Optional

from app.validators.date_validator import normalize_date, repair_ocr_date_text


def normalize_ocr_date(value: Optional[str]) -> Optional[str]:
    """
    Return a display/extracted date string repaired from OCR noise.
    Prefers a value that also parses via normalize_date.
    """
    if not value:
        return None
    text = repair_ocr_date_text(value)
    parsed = normalize_date(text) or normalize_date(value)
    if parsed:
        # Prefer original separator style when present
        match = re.search(
            r"\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}"
            r"|\d{1,2}-[0-9]{1,2}[./]\d{2,4}"
            r"|\d{1,2}[-/\s]+[A-Za-z]{3,9}[-/\s]+\d{2,4}",
            text,
        )
        if match and normalize_date(match.group(0)):
            return match.group(0).replace(".", "/")
        return parsed.strftime("%d/%m/%Y")
    match = re.search(r"\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}", text)
    return match.group(0).replace(".", "/") if match else None
