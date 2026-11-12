from __future__ import annotations

import re
from typing import Optional

from rapidfuzz import fuzz

from app.models.verification_models import FieldComparisonResult


_PUNCT_RE = re.compile(r"[^\w\s]")
_SPACE_RE = re.compile(r"\s+")


def normalize_party_name(value: Optional[str]) -> str:
    if not value:
        return ""
    text = value.upper().strip()
    text = text.replace("&", " AND ")
    text = _PUNCT_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    # Collapse spaced initials: "A M COOLING" -> "AM COOLING"
    text = re.sub(r"\b([A-Z])\s+(?=[A-Z]\b)", r"\1", text)
    text = re.sub(r"\b([A-Z])\s+(?=[A-Z]{2,})", r"\1", text)
    # Common abbreviation normalization
    text = text.replace(" PRIVATE LIMITED", " PVT LTD")
    text = text.replace(" LIMITED", " LTD")
    text = _SPACE_RE.sub(" ", text).strip()
    return text


def compare_party_names(
    expected: Optional[str],
    extracted: Optional[str],
    *,
    fuzzy_threshold: int = 85,
    field_name: str = "partyName",
) -> FieldComparisonResult:
    if not expected or not extracted:
        return FieldComparisonResult(
            field_name=field_name,
            expected=expected,
            extracted=extracted,
            matched=False,
            status="INSUFFICIENT_DATA",
            explanation="Party name missing on transaction or document",
        )

    n_exp = normalize_party_name(expected)
    n_ext = normalize_party_name(extracted)
    if not n_exp or not n_ext:
        return FieldComparisonResult(
            field_name=field_name,
            expected=expected,
            extracted=extracted,
            matched=False,
            status="INSUFFICIENT_DATA",
            explanation="Party name empty after normalization",
        )

    if n_exp == n_ext:
        score = 100.0
        matched = True
        status = "MATCH"
        explanation = "Exact normalized party name match"
    elif n_exp in n_ext or n_ext in n_exp:
        score = 95.0
        matched = True
        status = "MATCH"
        explanation = "Party name contained in the other value"
    else:
        score = float(fuzz.token_set_ratio(n_exp, n_ext))
        if score >= fuzzy_threshold:
            # Fuzzy-only hits are not auto-approved as hard matches
            matched = False
            status = "AMBIGUOUS"
            explanation = (
                f"Fuzzy score {score:.1f} >= {fuzzy_threshold} but not exact; "
                "requires manual review"
            )
        else:
            matched = False
            status = "MISMATCH"
            explanation = f"Fuzzy score {score:.1f} below threshold {fuzzy_threshold}"

    return FieldComparisonResult(
        field_name=field_name,
        expected=expected,
        extracted=extracted,
        matched=matched,
        score=score,
        status=status,
        explanation=explanation,
    )