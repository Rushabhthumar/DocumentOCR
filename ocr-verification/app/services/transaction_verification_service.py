from __future__ import annotations

import logging
import threading
from decimal import Decimal
from typing import Optional

from app.config import Settings, get_settings
from app.models.document_models import DocumentType
from app.models.extraction_models import ExtractionResult
from app.models.verification_models import (
    DocumentVerificationResult,
    FieldComparisonResult,
    TransactionSnapshot,
    VerificationDecision,
    VerificationResult,
)
from app.services.extraction_service import ExtractionService
from app.services.extractors.ocr_fuzzy_match import amount_mentioned_in_ocr, date_mentioned_in_ocr
from app.services.extractors.utr_extractor import payment_row_to_fields, select_matching_payment_row
from app.services.file_service import FileService
from app.services.history_service import HistoryService
from app.services.transaction_service import TransactionService
from app.validators import compare_amounts, compare_dates, compare_descriptions, compare_party_names
from app.validators.amount_validator import normalize_amount

logger = logging.getLogger(__name__)

_FIELD_LABELS = {
    "amount": "Amount",
    "date": "Date",
    "documentDate": "Date",
    "invoiceDate": "Date",
    "partyName": "Party name",
    "description": "Description",
}


class TransactionVerificationService:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        transaction_service: Optional[TransactionService] = None,
        file_service: Optional[FileService] = None,
        extraction_service: Optional[ExtractionService] = None,
        history_service: Optional[HistoryService] = None,
    ):
        self.settings = settings or get_settings()
        self.transaction_service = transaction_service or TransactionService(self.settings)
        self.file_service = file_service or FileService(self.settings)
        self.extraction_service = extraction_service or ExtractionService(self.settings)
        self.history_service = history_service or HistoryService(self.settings)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, transaction_id: str) -> threading.Lock:
        with self._locks_guard:
            if transaction_id not in self._locks:
                self._locks[transaction_id] = threading.Lock()
            return self._locks[transaction_id]

    def verify(self, transaction_id: str, *, force: bool = False) -> VerificationResult:
        lock = self._lock_for(transaction_id)
        if not lock.acquire(blocking=False):
            return VerificationResult(
                transaction_id=transaction_id,
                decision=VerificationDecision.MANUAL_REVIEW,
                message="Verification already in progress for this transaction",
                warnings=["Duplicate/concurrent verification request blocked"],
            )

        temp_docs = []
        try:
            if not force:
                latest = self.history_service.latest(transaction_id)
                if latest and latest.get("decision") == VerificationDecision.VERIFIED.value:
                    # Idempotent: already verified
                    latest["message"] = latest.get("message") or "Already verified previously"
                    return VerificationResult.model_validate(latest)

            try:
                raw_txn = self.transaction_service.get_transaction(transaction_id)
            except LookupError as exc:
                result = VerificationResult(
                    transaction_id=transaction_id,
                    decision=VerificationDecision.DOCUMENT_NOT_FOUND,
                    message=str(exc),
                    errors=[str(exc)],
                )
                self.history_service.save(result)
                return result
            except Exception as exc:  # noqa: BLE001
                result = VerificationResult(
                    transaction_id=transaction_id,
                    decision=VerificationDecision.MANUAL_REVIEW,
                    message=f"Failed to fetch transaction from Finance API: {exc}",
                    errors=[str(exc)],
                )
                self.history_service.save(result)
                return result

            snapshot = self.transaction_service.to_snapshot(raw_txn)
            if not snapshot.file_ids:
                result = VerificationResult(
                    transaction_id=transaction_id,
                    decision=VerificationDecision.DOCUMENT_NOT_FOUND,
                    transaction=snapshot,
                    message="No supporting file IDs found on transaction attachments",
                )
                self.history_service.save(result)
                return result

            doc_results: list[DocumentVerificationResult] = []
            extractions: list[ExtractionResult] = []

            for attachment in snapshot.attachments:
                file_id = attachment.get("fileId")
                if file_id is None or file_id == "" or file_id == 0:
                    continue
                filename = attachment.get("name")
                try:
                    binary = self.file_service.download_file(file_id, filename=filename)
                    temp_docs.append(binary)
                    extraction = self.extraction_service.extract_file(
                        binary.local_path,
                        file_id=str(file_id),
                        filename=filename or binary.filename,
                    )
                except FileNotFoundError as exc:
                    doc_results.append(
                        DocumentVerificationResult(
                            file_id=str(file_id),
                            filename=filename,
                            decision=VerificationDecision.DOCUMENT_NOT_FOUND,
                            warnings=[str(exc)],
                        )
                    )
                    continue
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Failed processing file %s", file_id)
                    doc_results.append(
                        DocumentVerificationResult(
                            file_id=str(file_id),
                            filename=filename,
                            decision=VerificationDecision.EXTRACTION_FAILED,
                            warnings=[str(exc)],
                        )
                    )
                    continue

                extractions.append(extraction)
                doc_results.append(
                    DocumentVerificationResult(
                        file_id=str(file_id),
                        filename=filename,
                        document_type=extraction.document_type.value,
                        extraction=extraction,
                        decision=VerificationDecision.MANUAL_REVIEW,
                        warnings=list(extraction.warnings),
                    )
                )

            if any(d.decision == VerificationDecision.DOCUMENT_NOT_FOUND for d in doc_results) and not extractions:
                result = VerificationResult(
                    transaction_id=transaction_id,
                    decision=VerificationDecision.DOCUMENT_NOT_FOUND,
                    transaction=snapshot,
                    documents=doc_results,
                    message="Supporting documents could not be downloaded",
                )
                self.history_service.save(result)
                return result

            if not extractions or all(e.extraction_status == "FAILED" for e in extractions):
                result = VerificationResult(
                    transaction_id=transaction_id,
                    decision=VerificationDecision.EXTRACTION_FAILED,
                    transaction=snapshot,
                    documents=doc_results,
                    message="Document extraction failed",
                )
                self.history_service.save(result)
                return result

            comparisons, decision, warnings = self._compare_all(snapshot, extractions)
            for doc in doc_results:
                if doc.extraction and doc.decision not in {
                    VerificationDecision.DOCUMENT_NOT_FOUND,
                    VerificationDecision.EXTRACTION_FAILED,
                }:
                    doc.decision = decision
                    doc.field_comparisons = comparisons

            status_updated = False
            verified_flag_set = False
            errors: list[str] = []

            if decision == VerificationDecision.VERIFIED:
                try:
                    self.transaction_service.mark_verified_by_ocr(transaction_id, raw_txn)
                    status_updated = True
                    verified_flag_set = True
                    message = "Verified. Appended OCR to narration."
                except Exception as exc:  # noqa: BLE001
                    decision = VerificationDecision.MANUAL_REVIEW
                    warnings.append(f"Verification matched but status update failed: {exc}")
                    message = f"Matched fields but failed to update transaction: {exc}"
                    errors = [message]
                    status_updated = False
            else:
                reasons = self._failure_reasons(comparisons, warnings, decision)
                message = "; ".join(reasons) if reasons else f"Decision {decision.value}"
                errors = list(reasons)

            result = VerificationResult(
                transaction_id=transaction_id,
                decision=decision,
                status_updated=status_updated,
                verified_flag_set=verified_flag_set,
                transaction=snapshot.model_copy(update={"raw": {}}),
                documents=doc_results,
                field_comparisons=comparisons,
                warnings=warnings,
                errors=errors,
                message=message,
            )
            self.history_service.save(result)
            return result
        finally:
            for binary in temp_docs:
                self.file_service.cleanup(binary)
            lock.release()

    @staticmethod
    def _failure_reasons(
        comparisons: list[FieldComparisonResult],
        warnings: list[str],
        decision: VerificationDecision,
    ) -> list[str]:
        """Build clear human-readable failure reasons (amount/date/party mismatch, etc.)."""
        reasons: list[str] = []
        for cmp in comparisons:
            label = _FIELD_LABELS.get(cmp.field_name, cmp.field_name)
            if cmp.status == "MISMATCH":
                reasons.append(
                    f"{label} mismatch: expected {cmp.expected}, document {cmp.extracted}"
                )
            elif cmp.status == "CONFLICT":
                detail = cmp.explanation or cmp.extracted or "conflicting values"
                reasons.append(f"{label} conflict: {detail}")
            elif cmp.status == "INSUFFICIENT_DATA":
                reasons.append(
                    f"{label} missing: {cmp.explanation or 'not found on transaction or document'}"
                )
            elif cmp.status == "AMBIGUOUS":
                reasons.append(
                    f"{label} ambiguous: {cmp.explanation or 'could not uniquely determine value'}"
                )

        extra_keywords = (
            "conflicting document",
            "low confidence",
            "could not uniquely match",
            "status update failed",
            "supporting documents",
            "extraction failed",
        )
        for warning in warnings:
            lowered = warning.lower()
            if any(key in lowered for key in extra_keywords):
                if warning not in reasons:
                    reasons.append(warning)

        if not reasons:
            reasons.append(f"Verification requires review ({decision.value})")
        return reasons

    def _compare_all(
        self,
        snapshot: TransactionSnapshot,
        extractions: list[ExtractionResult],
    ) -> tuple[list[FieldComparisonResult], VerificationDecision, list[str]]:
        warnings: list[str] = []
        comparisons: list[FieldComparisonResult] = []

        # Prepare candidate amount/date/party/description from documents
        amount_candidates: list[tuple[str, Decimal, float]] = []
        date_candidates: list[tuple[str, str, float]] = []
        party_candidates: list[tuple[str, str, float]] = []
        description_candidates: list[tuple[str, str, float]] = []

        for extraction in extractions:
            if extraction.document_type == DocumentType.UTR_STATEMENT or extraction.payment_rows:
                row, row_warnings = select_matching_payment_row(
                    extraction.payment_rows,
                    amount=snapshot.amount,
                    txn_date=snapshot.date,
                    utr_candidates=snapshot.utr_candidates,
                )
                warnings.extend([f"file {extraction.file_id}: {w}" for w in row_warnings])
                if row is None:
                    warnings.append(
                        f"file {extraction.file_id}: could not uniquely match UTR payment row"
                    )
                    continue
                # Overlay selected row fields onto extraction for reporting
                extraction.fields.update(payment_row_to_fields(row))
                if row.amount is not None:
                    amount_candidates.append((extraction.file_id, row.amount, row.confidence))
                if row.payment_date:
                    date_candidates.append((extraction.file_id, row.payment_date, row.confidence))
                if row.beneficiary_name:
                    party_candidates.append((extraction.file_id, row.beneficiary_name, row.confidence))
                if row.remark:
                    description_candidates.append((extraction.file_id, row.remark, row.confidence))
                continue

            amount_field = extraction.fields.get("amount")
            date_field = extraction.fields.get("documentDate") or extraction.fields.get("invoiceDate")
            party_field = extraction.fields.get("partyName") or extraction.fields.get("supplierName")
            desc_field = extraction.fields.get("description")

            amount_val = normalize_amount(amount_field.value) if amount_field else None
            # Drop absurd heuristic crumbs (14.00 / 11.00) that never match real bills
            amount_src = (amount_field.source or "") if amount_field else ""
            if (
                amount_val is not None
                and amount_val < Decimal("100")
                and "heuristic" in amount_src
            ):
                amount_val = None
                warnings.append(
                    f"file {extraction.file_id}: ignored tiny heuristic amount "
                    f"{amount_field.value if amount_field else None}"
                )
            if amount_val is not None:
                amount_candidates.append(
                    (extraction.file_id, amount_val, amount_field.confidence if amount_field else 0.0)
                )
            if date_field and date_field.value:
                date_candidates.append(
                    (extraction.file_id, str(date_field.value), date_field.confidence)
                )
            if party_field and party_field.value:
                party_candidates.append(
                    (extraction.file_id, str(party_field.value), party_field.confidence)
                )
            if desc_field and desc_field.value:
                description_candidates.append(
                    (extraction.file_id, str(desc_field.value), desc_field.confidence)
                )

            # Fuzzy recovery: noisy Hindi/handwritten OCR often misses structured fields
            # but still contains the transaction amount/date digits somewhere in raw text.
            raw_blob = "\n".join(
                [
                    extraction.raw_text or "",
                    *[(extraction.page_texts or {}).get(p) or "" for p in sorted(extraction.page_texts or {})],
                ]
            )
            from app.models.extraction_models import FieldValue

            # Handwritten bills often slip ₹1–5 or glue date digits; allow a small OCR window
            fuzzy_amt_tol = max(self.settings.amount_tolerance_decimal, Decimal("5.00"))
            if snapshot.amount is not None:
                fuzzy_amt = amount_mentioned_in_ocr(
                    raw_blob,
                    snapshot.amount,
                    tolerance=fuzzy_amt_tol,
                )
                if fuzzy_amt is not None:
                    amount_candidates.append((extraction.file_id, fuzzy_amt, 0.72))
                    warnings.append(
                        f"file {extraction.file_id}: amount {fuzzy_amt} recovered from noisy OCR text"
                    )
                    prev = normalize_amount(
                        extraction.fields["amount"].value
                        if extraction.fields.get("amount")
                        else None
                    )
                    if prev is None or abs(prev - snapshot.amount) > self.settings.amount_tolerance_decimal:
                        extraction.fields["amount"] = FieldValue(
                            value=str(fuzzy_amt),
                            confidence=0.72,
                            source="ocr_fuzzy_amount",
                        )
            if snapshot.date is not None:
                fuzzy_date = date_mentioned_in_ocr(raw_blob, snapshot.date)
                if fuzzy_date:
                    date_candidates.append((extraction.file_id, fuzzy_date, 0.7))
                    warnings.append(
                        f"file {extraction.file_id}: date {fuzzy_date} recovered from noisy OCR text"
                    )
                    from app.validators.date_validator import normalize_date as _norm_date

                    prev_day = (
                        _norm_date(date_field.value)
                        if date_field and date_field.value
                        else None
                    )
                    txn_day = _norm_date(snapshot.date)
                    if prev_day is None or (txn_day is not None and prev_day != txn_day):
                        extraction.fields["documentDate"] = FieldValue(
                            value=fuzzy_date,
                            confidence=0.7,
                            source="ocr_fuzzy_date",
                        )

        # Prefer any extracted amount that matches the transaction (common when
        # multiple attachments include GRN + invoice with different printed totals).
        selected_amount = None
        if amount_candidates:
            if snapshot.amount is not None:
                tol = self.settings.amount_tolerance_decimal
                matching = [
                    a for a in amount_candidates if abs(a[1] - snapshot.amount) <= tol
                ]
                if matching:
                    selected_amount = matching[0][1]
                    if len({str(a[1]) for a in amount_candidates}) > 1:
                        warnings.append(
                            "Multiple document amounts found; selected the one matching "
                            f"transaction amount {snapshot.amount}"
                        )
                else:
                    # One more pass over all raw texts before accepting mismatch
                    joined = "\n".join(e.raw_text or "" for e in extractions)
                    fuzzy = amount_mentioned_in_ocr(
                        joined,
                        snapshot.amount,
                        tolerance=max(tol, Decimal("5.00")),
                    )
                    if fuzzy is not None:
                        selected_amount = fuzzy
                        warnings.append(
                            f"Amount {fuzzy} recovered from combined OCR text against transaction"
                        )
                    else:
                        unique_amounts = {str(a[1]) for a in amount_candidates}
                        if len(unique_amounts) > 1:
                            warnings.append(
                                f"Conflicting document amounts found: {sorted(unique_amounts)}"
                            )
                            comparisons.append(
                                FieldComparisonResult(
                                    field_name="amount",
                                    expected=str(snapshot.amount),
                                    extracted=",".join(sorted(unique_amounts)),
                                    matched=False,
                                    status="CONFLICT",
                                    explanation="Multiple documents have conflicting amounts",
                                )
                            )
                            return comparisons, VerificationDecision.MANUAL_REVIEW, warnings
                        selected_amount = amount_candidates[0][1]
            else:
                selected_amount = amount_candidates[0][1]
        elif snapshot.amount is not None:
            joined = "\n".join(e.raw_text or "" for e in extractions)
            fuzzy = amount_mentioned_in_ocr(
                joined,
                snapshot.amount,
                tolerance=max(self.settings.amount_tolerance_decimal, Decimal("5.00")),
            )
            if fuzzy is not None:
                selected_amount = fuzzy
                warnings.append(
                    f"Amount {fuzzy} recovered from combined OCR text against transaction"
                )

        # Prefer a document date that matches the transaction (Due Date vs Invoice Date)
        selected_date = None
        if date_candidates:
            if snapshot.date is not None:
                from app.validators.date_validator import normalize_date

                txn_day = normalize_date(snapshot.date)
                matching_dates = []
                for file_id, raw_date, conf in date_candidates:
                    doc_day = normalize_date(raw_date)
                    if txn_day and doc_day and abs((txn_day - doc_day).days) <= self.settings.date_tolerance_days:
                        matching_dates.append((file_id, raw_date, conf))
                if matching_dates:
                    selected_date = matching_dates[0][1]
                    if len({d[1] for d in date_candidates}) > 1:
                        warnings.append(
                            "Multiple document dates found; selected the one matching "
                            f"transaction date {txn_day}"
                        )
                else:
                    # Fuzzy search before accepting a wrong/ unrelated date
                    joined = "\n".join(e.raw_text or "" for e in extractions)
                    fuzzy_date = date_mentioned_in_ocr(joined, snapshot.date)
                    selected_date = fuzzy_date or date_candidates[0][1]
                    if fuzzy_date:
                        warnings.append(
                            f"Date {fuzzy_date} recovered from OCR text against transaction"
                        )
            else:
                selected_date = date_candidates[0][1]
        elif snapshot.date is not None:
            joined = "\n".join(e.raw_text or "" for e in extractions)
            fuzzy_date = date_mentioned_in_ocr(joined, snapshot.date)
            if fuzzy_date:
                selected_date = fuzzy_date
                warnings.append(
                    f"Date {fuzzy_date} recovered from OCR text against transaction"
                )
        selected_party = party_candidates[0][1] if party_candidates else None
        selected_description = description_candidates[0][1] if description_candidates else None

        # Low confidence gate
        low_conf = False
        for extraction in extractions:
            page_conf = (extraction.metadata or {}).get("page_confidences") or {}
            for conf in page_conf.values():
                if conf and float(conf) < self.settings.min_ocr_confidence:
                    # Text-extracted pages have conf 1.0; OCR pages may be low
                    if float(conf) < 0.999:  # OCR path
                        low_conf = True
        if low_conf:
            warnings.append("One or more OCR pages have low confidence")

        amount_cmp = compare_amounts(
            snapshot.amount,
            selected_amount,
            tolerance=self.settings.amount_tolerance_decimal,
        )
        date_cmp = compare_dates(
            snapshot.date,
            selected_date,
            tolerance_days=self.settings.date_tolerance_days,
        )
        comparisons.extend([amount_cmp, date_cmp])

        # Party: only mandatory when transaction has a resolvable party name
        if snapshot.party_name:
            party_cmp = compare_party_names(
                snapshot.party_name,
                selected_party,
                fuzzy_threshold=self.settings.party_fuzzy_threshold,
            )
            comparisons.append(party_cmp)
        else:
            party_cmp = FieldComparisonResult(
                field_name="partyName",
                expected=None,
                extracted=selected_party,
                matched=True,
                status="SKIPPED",
                explanation=(
                    "Transaction has no payee/party name field available from "
                    "bankTransactionDetails; party check skipped"
                ),
            )
            comparisons.append(party_cmp)
            warnings.append(party_cmp.explanation)

        if self.settings.enable_description_check:
            desc_cmp = compare_descriptions(snapshot.narration, selected_description)
        else:
            desc_cmp = FieldComparisonResult(
                field_name="description",
                expected=snapshot.narration,
                extracted=selected_description,
                matched=True,
                status="SKIPPED",
                explanation="Description check disabled for now; will be enabled later",
            )
            warnings.append(desc_cmp.explanation)
        comparisons.append(desc_cmp)

        # Decision engine
        mandatory = [amount_cmp, date_cmp]
        if snapshot.party_name:
            mandatory.append(party_cmp)

        if any(c.status == "CONFLICT" for c in comparisons):
            return comparisons, VerificationDecision.MANUAL_REVIEW, warnings

        if any(c.status == "MISMATCH" for c in mandatory):
            return comparisons, VerificationDecision.MISMATCH, warnings

        if any(c.status in {"INSUFFICIENT_DATA", "AMBIGUOUS"} for c in mandatory):
            return comparisons, VerificationDecision.MANUAL_REVIEW, warnings

        if self.settings.enable_description_check:
            if desc_cmp.status == "MISMATCH":
                warnings.append("Description mismatch marked for manual review")
                return comparisons, VerificationDecision.MANUAL_REVIEW, warnings
            if desc_cmp.status in {"INSUFFICIENT_DATA", "AMBIGUOUS"}:
                warnings.append("Description evidence insufficient/ambiguous")
                return comparisons, VerificationDecision.MANUAL_REVIEW, warnings

        if low_conf:
            return comparisons, VerificationDecision.MANUAL_REVIEW, warnings

        if not amount_candidates or not date_candidates:
            return comparisons, VerificationDecision.MANUAL_REVIEW, warnings

        # User rule: document total(s) match txn amount AND payment/document date matches txn date
        description_ok = (
            not self.settings.enable_description_check
            or desc_cmp.matched
            or desc_cmp.status == "SKIPPED"
        )
        if (
            amount_cmp.matched
            and date_cmp.matched
            and party_cmp.status in {"MATCH", "SKIPPED"}
            and description_ok
        ):
            return comparisons, VerificationDecision.VERIFIED, warnings

        return comparisons, VerificationDecision.MANUAL_REVIEW, warnings