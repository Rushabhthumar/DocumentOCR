from __future__ import annotations

import re
from typing import Optional

from rapidfuzz import fuzz

from app.models.verification_models import FieldComparisonResult


_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]")


def normalize_description(value: Optional[str]) -> str:
    if not value:
        return ""
    text = value.upper().strip()
    text = _PUNCT_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    return text


def compare_descriptions(
    expected: Optional[str],
    extracted: Optional[str],
    *,
    fuzzy_threshold: int = 70,
    field_name: str = "description",
) -> FieldComparisonResult:
    if not expected or not extracted:
        return FieldComparisonResult(
            field_name=field_name,
            expected=expected,
            extracted=extracted,
            matched=False,
            status="INSUFFICIENT_DATA",
            explanation="Description/narration missing on transaction or document",
        )

    n_exp = normalize_description(expected)
    n_ext = normalize_description(extracted)
    if not n_exp or not n_ext:
        return FieldComparisonResult(
            field_name=field_name,
            expected=expected,
            extracted=extracted,
            matched=False,
            status="INSUFFICIENT_DATA",
            explanation="Description empty after normalization",
        )

    if n_exp == n_ext:
        return FieldComparisonResult(
            field_name=field_name,
            expected=expected,
            extracted=extracted,
            matched=True,
            score=100.0,
            status="MATCH",
            explanation="Exact normalized description match",
        )

    # Token overlap / containment is stronger evidence than pure fuzzy
    exp_tokens = set(n_exp.split())
    ext_tokens = set(n_ext.split())
    if exp_tokens and exp_tokens.issubset(ext_tokens):
        return FieldComparisonResult(
            field_name=field_name,
            expected=expected,
            extracted=extracted,
            matched=True,
            score=92.0,
            status="MATCH",
            explanation="Transaction description tokens found in document text",
        )
    if ext_tokens and ext_tokens.issubset(exp_tokens):
        return FieldComparisonResult(
            field_name=field_name,
            expected=expected,
            extracted=extracted,
            matched=True,
            score=90.0,
            status="MATCH",
            explanation="Document description tokens found in transaction narration",
        )

    score = float(fuzz.token_set_ratio(n_exp, n_ext))
    if score >= fuzzy_threshold:
        return FieldComparisonResult(
            field_name=field_name,
            expected=expected,
            extracted=extracted,
            matched=False,
            score=score,
            status="AMBIGUOUS",
            explanation=(
                f"Description fuzzy score {score:.1f} is suggestive but not conclusive; "
                "manual review required"
            ),
        )

    return FieldComparisonResult(
        field_name=field_name,
        expected=expected,
        extracted=extracted,
        matched=False,
        score=score,
        status="MISMATCH",
        explanation=f"Description fuzzy score {score:.1f} below threshold {fuzzy_threshold}",
    )