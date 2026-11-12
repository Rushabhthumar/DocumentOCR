from __future__ import annotations

import re
from typing import Optional

from app.models.document_models import ClassificationResult, ClassifiedPage, DocumentType


class DocumentClassifier:
    """Rule-based classifier using extracted text and filename hints."""

    CASH_MEMO_SIGNALS = [
        r"\bcash\s*memo\b",
        r"\bcash\b[\s\S]{0,20}\bmemo\b",
        r"\bgstin\b",
        r"\bauthori[sz]ed\s*s[iy]gnature\b",
        r"\bcustomer\s*signature\b",
        r"\btotal\b[\s\S]{0,40}\bbalance\b",
        r"\ba\s*m\s*cooling\s*systems\b",
    ]
    GRN_SIGNALS = [
        r"\bgoods\s*received\s*note\b",
        r"\bgrn\s*no\b",
        r"\bgate\s*entry\s*no\b",
        r"\bsupplier\s*name\b",
        r"\bmaterial\s*description\b",
    ]
    UTR_SIGNALS = [
        r"\butr\s*no\b",
        r"\bpymt_mode\b",
        r"\bbeneficiary\s*name\b",
        r"\bbene_ifsc_code\b",
        r"\bpymt_date\b",
        r"\bneft\b",
    ]

    def _score(self, text: str, patterns: list[str]) -> tuple[float, list[str]]:
        hits: list[str] = []
        lowered = text.lower()
        for pattern in patterns:
            if re.search(pattern, lowered, flags=re.IGNORECASE):
                hits.append(pattern)
        if not patterns:
            return 0.0, hits
        return len(hits) / len(patterns), hits

    def classify_page_text(self, text: str, page_number: int = 1) -> ClassifiedPage:
        scores = {
            DocumentType.CASH_MEMO: self._score(text, self.CASH_MEMO_SIGNALS),
            DocumentType.GRN: self._score(text, self.GRN_SIGNALS),
            DocumentType.UTR_STATEMENT: self._score(text, self.UTR_SIGNALS),
        }
        best_type = DocumentType.UNKNOWN
        best_score = 0.0
        signals: list[str] = []
        for doc_type, (score, hits) in scores.items():
            if score > best_score:
                best_score = score
                best_type = doc_type
                signals = hits
        if best_score < 0.2:
            best_type = DocumentType.UNKNOWN
        return ClassifiedPage(
            page_number=page_number,
            document_type=best_type,
            confidence=best_score,
            signals=signals,
        )

    def classify_document(
        self,
        page_texts: dict[int, str],
        filename: Optional[str] = None,
    ) -> ClassificationResult:
        pages = [
            self.classify_page_text(text, page_number=page_no)
            for page_no, text in sorted(page_texts.items())
        ]
        if not pages and filename:
            lower = filename.lower()
            if "utr" in lower or "payment" in lower:
                return ClassificationResult(
                    primary_type=DocumentType.UTR_STATEMENT,
                    confidence=0.4,
                    raw_signals={"filename": filename},
                )
            if "othertype" in lower or "cash" in lower:
                return ClassificationResult(
                    primary_type=DocumentType.MIXED,
                    confidence=0.3,
                    raw_signals={"filename": filename},
                )

        type_counts: dict[DocumentType, int] = {}
        for page in pages:
            type_counts[page.document_type] = type_counts.get(page.document_type, 0) + 1

        non_unknown = {k: v for k, v in type_counts.items() if k != DocumentType.UNKNOWN}
        if not non_unknown:
            primary = DocumentType.UNKNOWN
            confidence = 0.0
        elif len(non_unknown) == 1:
            primary = next(iter(non_unknown))
            confidence = max((p.confidence for p in pages if p.document_type == primary), default=0.0)
        else:
            primary = DocumentType.MIXED
            confidence = max((p.confidence for p in pages), default=0.0)

        return ClassificationResult(
            primary_type=primary,
            pages=pages,
            confidence=confidence,
            raw_signals={"type_counts": {k.value: v for k, v in type_counts.items()}},
        )