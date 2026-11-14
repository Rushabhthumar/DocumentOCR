from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional


_ONES = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}

# Common OCR misspellings seen on cash bills / handwritten totals
_OCR_FIXES = {
    "seveerteen": "seventeen",
    "seveteen": "seventeen",
    "seventen": "seventeen",
    "rwo": "two",
    "tow": "two",
    "froordeen": "fourteen",
    "froorteen": "fourteen",
    "frousdeen": "fourteen",
    "frourteen": "fourteen",
    "forteen": "fourteen",
    "ony": "only",
    "onyl": "only",
    "fourty": "forty",
    "ninty": "ninety",
    "nintyen": "nineteen",
    "hundered": "hundred",
    "hunderd": "hundred",
    "hundrad": "hundred",
    "thousend": "thousand",
    "thosand": "thousand",
    "thosund": "thousand",
    "jhousand": "thousand",
    "lakh": "lakh",
    "lac": "lakh",
    "indian": "",
    "ldian": "",
    "only": "",
    "rupees": "",
    "rupee": "",
    "rs": "",
    "inr": "",
}

_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

_SCALES = {
    "hundred": 100,
    "thousand": 1000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
}

# Allow trailing tens/ones after hundred/thousand (…Nine Hundred Sixty Only)
_WORD_TAIL = (
    r"(?:hundred|thousand|lakh|lakhs|crore|crores)"
    r"(?:\s+(?:and\s+)?"
    r"(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)?"
    r"(?:[\s\-]*(?:one|two|three|four|five|six|seven|eight|nine))?"
    r")?"
    r"(?:\s+only)?"
)

_WORDS_BLOCK = re.compile(
    rf"(?i)(?:rupees?|rs\.?|inr|indian\s+rupees?)?\s*[:=]?\s*"
    rf"((?:[a-z]{{2,}}[\s\-]+){{1,16}}{_WORD_TAIL})"
)


def _normalize_token(token: str) -> str:
    t = re.sub(r"[^a-z]", "", token.lower())
    return _OCR_FIXES.get(t, t)


def words_to_amount(text: str) -> Optional[Decimal]:
    """
    Convert noisy Indian-English amount-in-words to Decimal.
    Handles OCR misspellings common on cash bills.
    """
    if not text:
        return None
    raw = re.sub(r"(?i)\b(rupees?|rs\.?|inr|only|/|indian)\b", " ", text)
    raw = re.sub(r"[^a-zA-Z\s\-]", " ", raw)
    tokens = [_normalize_token(t) for t in re.split(r"[\s\-]+", raw) if t.strip()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return None

    total = 0
    current = 0
    saw_number = False
    for token in tokens:
        if token in _ONES:
            current += _ONES[token]
            saw_number = True
        elif token in _TENS:
            current += _TENS[token]
            saw_number = True
        elif token in _SCALES:
            scale = _SCALES[token]
            if current == 0:
                current = 1
            current *= scale
            if scale >= 1000:
                total += current
                current = 0
            saw_number = True
        elif token in {"and", "a"}:
            continue
        else:
            continue
    total += current
    if not saw_number or total <= 0:
        return None
    return Decimal(total).quantize(Decimal("0.01"))


def extract_amount_from_words(text: str) -> Optional[Decimal]:
    """Find an amount-in-words phrase in OCR text and parse it."""
    if not text:
        return None
    compact = re.sub(r"\s+", " ", text)
    # Prefer phrases near total / amount labels — do NOT stop at first "hundred"
    labeled = re.search(
        r"(?i)(?:total|amount|grand\s*total|bill\s*amount|invoice\s*amount|"
        r"amount\s*(?:in\s*)?words|in\s*words|indian\s+rupees?)\s*[:\-]?\s*"
        r"(?:rupees?|rs\.?|indian\s+rupees?)?\s*"
        rf"([A-Za-z][A-Za-z\s\-]{{8,160}}?{_WORD_TAIL})",
        compact,
    )
    candidates: list[str] = []
    if labeled:
        candidates.append(labeled.group(1))
    for match in _WORDS_BLOCK.finditer(compact):
        candidates.append(match.group(1))
    # Also try last ~250 chars (totals often at bottom of cash bills)
    candidates.append(compact[-250:])

    best: Optional[Decimal] = None
    for cand in candidates:
        amount = words_to_amount(cand)
        if amount is None:
            continue
        if amount < Decimal("1.00") or amount > Decimal("100000000"):
            continue
        if best is None or amount > best:
            best = amount
    return best
