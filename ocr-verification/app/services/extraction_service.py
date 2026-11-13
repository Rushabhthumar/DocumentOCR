from __future__ import annotations

import logging
import re
from decimal import Decimal
from pathlib import Path
from typing import Optional

from app.config import Settings, get_settings
from app.models.document_models import DocumentType, PageExtractionMode
from app.models.extraction_models import ExtractionResult, FieldValue
from app.services.document_classifier import DocumentClassifier
from app.services.extractors import (
    extract_cash_memo_fields,
    extract_generic_fields,
    extract_grn_fields,
    extract_invoice_fields,
    extract_payment_rows,
    payment_row_to_fields,
)
from app.services.extractors.amount_total import amounts_equivalent, extract_grand_total
from app.services.image_service import ImageService
from app.services.ocr_service import OcrService
from app.services.pdf_service import PdfService
from app.validators.amount_validator import normalize_amount

# Document types that share invoice-like amount/date layout
_INVOICE_LIKE = {
    DocumentType.INVOICE,
    DocumentType.RECEIPT,
    DocumentType.VOUCHER,
    DocumentType.BILL,
    DocumentType.CHALLAN,
    DocumentType.EWAY_BILL,
    DocumentType.UNKNOWN,
}

# Pages that carry the real payable bill total
_BILL_PAGE_TYPES = {
    DocumentType.INVOICE,
    DocumentType.CASH_MEMO,
    DocumentType.RECEIPT,
    DocumentType.VOUCHER,
    DocumentType.BILL,
    DocumentType.UNKNOWN,
}

# Supporting pages: same grand total as bill, must NOT be summed
_SUPPORT_PAGE_TYPES = {
    DocumentType.EWAY_BILL,
    DocumentType.CHALLAN,
}

logger = logging.getLogger(__name__)


class ExtractionService:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        pdf_service: Optional[PdfService] = None,
        ocr_service: Optional[OcrService] = None,
        image_service: Optional[ImageService] = None,
        classifier: Optional[DocumentClassifier] = None,
    ):
        self.settings = settings or get_settings()
        self.pdf_service = pdf_service or PdfService(self.settings)
        self.ocr_service = ocr_service or OcrService(self.settings)
        self.image_service = image_service or ImageService()
        self.classifier = classifier or DocumentClassifier()

    def _should_use_structure(self, hint_text: str = "") -> bool:
        if not self.settings.enable_structure_ocr:
            return False
        if self.settings.structure_ocr_for_all_pages:
            return True
        # First-pass OCR text is usually empty for scans; decide after light OCR via caller.
        return bool(
            re.search(
                r"(?i)tax\s*invoice|invoice\s*no|grand\s*total|hsn|cgst|sgst|table",
                hint_text or "",
            )
        )

    def _run_page_ocr(self, image, *, page_hint_text: str = ""):
        """
        Run PaddleOCR once, then selectively enrich with structure tables / TrOCR.
        Structure is expensive on CPU — only for invoice-like pages missing a clear total.
        """
        from app.services.extractors.amount_total import extract_grand_total

        base = self.ocr_service.run_ocr(
            image,
            preprocess=True,
            use_structure=False,
            use_trocr=False,
        )
        text = base.text or page_hint_text or ""
        total, _ = extract_grand_total(text)
        use_structure = self._should_use_structure(text) and total is None
        if use_structure or self.settings.structure_ocr_for_all_pages:
            structure = self.ocr_service.run_structure(
                image.convert("RGB") if getattr(image, "mode", "RGB") != "RGB" else image
            )
            if structure:
                table_text = structure.get("table_text") or ""
                structure_text = structure.get("structure_text") or ""
                extras = [t for t in (table_text, structure_text) if t]
                if extras:
                    base.table_text = table_text
                    base.structure_text = structure_text
                    base.text = (base.text or "") + "\n\n--- STRUCTURE ---\n" + "\n".join(extras)
                    base.engine = f"{base.engine}+structure"
                    base.warnings.append("Merged PP-StructureV3 table/layout text")

        use_trocr = self.settings.enable_trocr_fallback and (
            self.ocr_service._looks_handwritten_or_noisy(base)
            or bool(re.search(r"(?i)cash\s*(?:bill|memo)|laundry|engineering", base.text or ""))
        )
        if use_trocr:
            base = self.ocr_service._apply_trocr_on_low_confidence(
                self.image_service.preprocess(image, for_handwriting=True).convert("RGB"),
                base,
            )
        return base

    def extract_file(
        self,
        path: str | Path,
        *,
        file_id: str,
        filename: Optional[str] = None,
    ) -> ExtractionResult:
        path = Path(path)
        suffix = path.suffix.lower()
        warnings: list[str] = []
        errors: list[str] = []
        page_texts: dict[int, str] = {}
        page_confidences: dict[int, float] = {}
        page_count = 0

        try:
            if suffix == ".pdf":
                pages = self.pdf_service.process_pdf(path)
                page_count = len(pages)
                for page in pages:
                    text = page.text or ""
                    conf = 1.0 if page.mode == PageExtractionMode.TEXT else 0.0
                    if page.mode == PageExtractionMode.OCR:
                        if page.image is None:
                            errors.append(f"Page {page.page_number}: OCR required but no image")
                            continue
                        try:
                            ocr_result = self._run_page_ocr(page.image, page_hint_text=text)
                            text = ocr_result.text
                            conf = ocr_result.average_confidence
                            warnings.extend(
                                [f"Page {page.page_number}: {w}" for w in ocr_result.warnings]
                            )
                        except Exception as exc:  # noqa: BLE001
                            errors.append(f"Page {page.page_number}: OCR failed: {exc}")
                            warnings.extend(page.warnings)
                            continue
                    elif page.mode == PageExtractionMode.FAILED:
                        errors.append(f"Page {page.page_number}: failed to process")
                        continue
                    page_texts[page.page_number] = text
                    page_confidences[page.page_number] = conf
                    warnings.extend(page.warnings)
            elif suffix in {".jpg", ".jpeg", ".png"}:
                page_count = 1
                image = self.image_service.load(path)
                try:
                    ocr_result = self._run_page_ocr(image)
                    page_texts[1] = ocr_result.text
                    page_confidences[1] = ocr_result.average_confidence
                    warnings.extend(ocr_result.warnings)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"Image OCR failed: {exc}")
            else:
                errors.append(f"Unsupported file extension: {suffix}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Extraction failed for %s", path)
            return ExtractionResult(
                file_id=file_id,
                filename=filename,
                extraction_status="FAILED",
                errors=[str(exc)],
                warnings=warnings,
            )

        if not page_texts and errors:
            return ExtractionResult(
                file_id=file_id,
                filename=filename,
                page_count=page_count,
                extraction_status="FAILED",
                errors=errors,
                warnings=warnings,
            )

        classification = self.classifier.classify_document(page_texts, filename=filename)
        raw_text = "\n\n".join(
            f"--- Page {num} ---\n{page_texts[num]}" for num in sorted(page_texts)
        )

        result = ExtractionResult(
            file_id=file_id,
            filename=filename,
            document_type=classification.primary_type,
            page_count=page_count,
            extraction_status="SUCCESS" if not errors else "PARTIAL",
            raw_text=raw_text,
            page_texts=page_texts,
            warnings=warnings,
            errors=errors,
            page_types={p.page_number: p.document_type for p in classification.pages},
            metadata={
                "classification_confidence": classification.confidence,
                "page_confidences": page_confidences,
            },
        )

        self._populate_structured_fields(result, page_confidences)
        return result

    @staticmethod
    def _merge_field_map(
        merged: dict[str, FieldValue],
        incoming: dict[str, FieldValue],
        *,
        overwrite_keys: Optional[set[str]] = None,
        skip_amount_null: bool = False,
    ) -> None:
        overwrite_keys = overwrite_keys or set()
        for key, value in incoming.items():
            if skip_amount_null and key == "amount" and value.value is None:
                if key not in merged:
                    merged[key] = value
                continue
            existing = merged.get(key)
            if value.value is None and existing is not None:
                continue
            # Keep first good value for identity fields unless explicitly overwriting
            if (
                existing
                and existing.value is not None
                and key not in overwrite_keys
                and key in {"amount", "documentDate", "documentNumber", "partyName", "invoiceDate"}
            ):
                continue
            if existing and existing.value is not None and key not in overwrite_keys:
                if value.value is None:
                    continue
            merged[key] = value

    def _populate_structured_fields(
        self,
        result: ExtractionResult,
        page_confidences: dict[int, float],
    ) -> None:
        page_types = dict(result.page_types or {})
        merged_fields: dict[str, FieldValue] = {}
        # Per-page grand totals for bill vs e-bill / support dedupe (never sum)
        page_totals: list[tuple[int, DocumentType, Decimal, float]] = []

        for page_no, text in sorted(result.page_texts.items()):
            page_type = page_types.get(page_no, result.document_type)
            conf = page_confidences.get(page_no, 0.0)

            # Reclassify obvious e-way / tax-invoice / cash-bill pages when UNKNOWN
            if page_type == DocumentType.UNKNOWN:
                if re.search(r"(?i)e[\-\s]?way\s*bill|total\s*inv\.?\s*amt", text):
                    page_type = DocumentType.EWAY_BILL
                    page_types[page_no] = page_type
                elif re.search(r"(?i)tax\s*invoice|invoice\s*no", text):
                    page_type = DocumentType.INVOICE
                    page_types[page_no] = page_type
                elif re.search(r"(?i)cash\s*(?:bill|memo)|cashmemo", text):
                    page_type = DocumentType.CASH_MEMO
                    page_types[page_no] = page_type
                elif re.search(r"(?i)laund(?:ry|ary)\s*register", text):
                    # Supporting handwritten register — not the payable bill page
                    page_type = DocumentType.UNKNOWN
                    page_types[page_no] = page_type
                    result.warnings.append(
                        f"Page {page_no}: laundry register treated as supporting page (no amount)"
                    )

            # Skip amount harvesting from laundry registers / pure handwritten tallies
            is_support_register = bool(
                re.search(r"(?i)laund(?:ry|ary)\s*register|monitor'?s?\s*sign", text)
            )

            # 1) Type-specific extractors
            if page_type == DocumentType.UTR_STATEMENT or (
                result.document_type == DocumentType.UTR_STATEMENT
                and page_type == DocumentType.UNKNOWN
            ):
                rows = extract_payment_rows(text, page_number=page_no)
                result.payment_rows.extend(rows)
                if rows and not any(
                    merged_fields.get(k) and merged_fields[k].value is not None
                    for k in ("amount", "documentDate")
                ):
                    merged_fields.update(payment_row_to_fields(rows[0]))
            elif page_type == DocumentType.CASH_MEMO:
                parsed = extract_cash_memo_fields(text, page_number=page_no, confidence=conf)
                self._merge_field_map(
                    merged_fields,
                    parsed["fields"],
                    overwrite_keys={"documentDate", "documentNumber", "partyName"},
                )
                result.items.extend(parsed["items"])
                result.warnings.extend(parsed["warnings"])
            elif page_type == DocumentType.GRN:
                parsed = extract_grn_fields(text, page_number=page_no, confidence=conf)
                # GRN has no payable total — never take quantity as amount.
                # Also do not overwrite bill party/date/doc-no with GRN boilerplate.
                grn_fields = {
                    k: v
                    for k, v in parsed["fields"].items()
                    if k not in {"amount", "partyName", "documentDate", "documentNumber"}
                }
                self._merge_field_map(merged_fields, grn_fields)
                result.items.extend(parsed["items"])
                result.warnings.extend(parsed["warnings"])
                result.warnings.append(
                    f"Page {page_no}: GRN ignored for amount (supporting doc only)"
                )
            elif page_type in _INVOICE_LIKE:
                if page_type != DocumentType.UNKNOWN or re.search(
                    r"(?i)tax\s*invoice|invoice\s*date|grand\s*total|billed\s*to|"
                    r"bill\s*amount|receipt\s*no|voucher\s*no|challan\s*no|"
                    r"total\s*inv\.?\s*amt|e[\-\s]?way\s*bill",
                    text,
                ):
                    parsed = extract_invoice_fields(text, page_number=page_no, confidence=conf)
                    # Merge non-amount fields; amount resolved across pages below
                    non_amount = {k: v for k, v in parsed["fields"].items() if k != "amount"}
                    self._merge_field_map(merged_fields, non_amount)
                    result.warnings.extend(parsed["warnings"])

            # 2) Fill non-amount gaps with generic (dates/party/doc no)
            generic = extract_generic_fields(text, page_number=page_no, confidence=conf)
            non_amount_generic = {k: v for k, v in generic["fields"].items() if k != "amount"}
            before_date = (
                merged_fields.get("documentDate").value if merged_fields.get("documentDate") else None
            )
            self._merge_field_map(merged_fields, non_amount_generic)
            if before_date is None and generic["fields"].get("documentDate") and generic["fields"]["documentDate"].value:
                result.warnings.extend(
                    [w for w in generic["warnings"] if "date" in w.lower() or "Date" in w]
                )

            # 3) Collect grand-total candidates per page (bill / e-bill). Do NOT sum.
            if (
                page_type != DocumentType.GRN
                and page_type != DocumentType.UTR_STATEMENT
                and not is_support_register
            ):
                total, src = extract_grand_total(text)
                if total is not None:
                    page_totals.append((page_no, page_type, total, conf))
                    result.warnings.append(
                        f"Page {page_no}: candidate total {total} via {src or 'pattern'}"
                    )

        # 4) Final amount authority: bill pages only (never GRN/register heuristics)
        bill_page_texts: list[str] = []
        for page_no, text in sorted(result.page_texts.items()):
            page_type = page_types.get(page_no, result.document_type)
            if page_type in {DocumentType.GRN, DocumentType.UTR_STATEMENT}:
                continue
            if re.search(r"(?i)laund(?:ry|ary)\s*register|monitor'?s?\s*sign", text):
                continue
            bill_page_texts.append(text)

        combined_bill_text = "\n".join(bill_page_texts)
        combined_total, combined_src = extract_grand_total(combined_bill_text)
        # Cash-memo / cash-bill column fallback when labels/words fail
        if combined_total is None and re.search(
            r"(?i)cash\s*(?:bill|memo)|cashmemo", combined_bill_text
        ):
            cash_parsed = extract_cash_memo_fields(combined_bill_text, confidence=0.7)
            cash_amt = cash_parsed["fields"].get("amount")
            if cash_amt and cash_amt.value:
                try:
                    combined_total = Decimal(str(cash_amt.value))
                    combined_src = cash_amt.source or "cash_memo_fallback"
                except Exception:  # noqa: BLE001
                    combined_total = None

        resolved = self._resolve_grand_total(page_totals, result)
        # Prefer combined bill-text total when it is stronger / present
        if combined_total is not None:
            if resolved is None or combined_total >= resolved or (
                combined_src and "words" in str(combined_src)
            ):
                resolved = combined_total
                result.warnings.append(
                    f"Final amount {resolved} from combined bill text ({combined_src})"
                )

        if resolved is not None:
            merged_fields["amount"] = FieldValue(
                value=str(resolved),
                confidence=max((c for _, _, _, c in page_totals), default=0.8),
                source=combined_src or "resolved_grand_total",
            )
        elif "amount" not in merged_fields or merged_fields["amount"].value is None:
            merged_fields["amount"] = FieldValue(
                value=None, confidence=0, reason="grand total not found"
            )

        result.page_types = page_types
        result.document_type = self._resolve_document_type(page_types, result)
        # Ensure cash bills are not left as Unknown after reclassification
        if result.document_type == DocumentType.UNKNOWN and any(
            t == DocumentType.CASH_MEMO for t in page_types.values()
        ):
            result.document_type = DocumentType.CASH_MEMO

        result.fields = merged_fields or {
            "documentNumber": FieldValue(value=None, confidence=0, reason="not extracted"),
            "documentDate": FieldValue(value=None, confidence=0, reason="not extracted"),
            "partyName": FieldValue(value=None, confidence=0, reason="not extracted"),
            "amount": FieldValue(value=None, confidence=0, reason="not extracted"),
            "description": FieldValue(value=None, confidence=0, reason="not extracted"),
        }
        result.metadata = {
            **(result.metadata or {}),
            "extractor_build": "2026-11-13-amount-words-v2",
            "amount_source": (merged_fields.get("amount").source if merged_fields.get("amount") else None),
        }

    def _resolve_grand_total(
        self,
        page_totals: list[tuple[int, DocumentType, Decimal, float]],
        result: ExtractionResult,
    ) -> Optional[Decimal]:
        """
        Pick a single payable grand total for the whole PDF.
        Bill page + e-bill/support page with the same total => one amount (never summed).
        """
        if not page_totals:
            return None

        bill = [(p, t, a, c) for p, t, a, c in page_totals if t in _BILL_PAGE_TYPES]
        support = [(p, t, a, c) for p, t, a, c in page_totals if t in _SUPPORT_PAGE_TYPES]
        other = [
            (p, t, a, c)
            for p, t, a, c in page_totals
            if t not in _BILL_PAGE_TYPES and t not in _SUPPORT_PAGE_TYPES
        ]

        chosen: Optional[Decimal] = None
        if bill:
            # Prefer the largest grand-total among bill pages (payable, not a tax line)
            chosen = max(a for _, _, a, _ in bill)
            result.warnings.append(
                f"Selected bill grand total {chosen} from page(s) "
                f"{sorted({p for p, _, a, _ in bill if a == chosen})}"
            )
        elif support:
            chosen = max(a for _, _, a, _ in support)
            result.warnings.append(f"Selected support-page grand total {chosen}")
        elif other:
            chosen = max(a for _, _, a, _ in other)

        if chosen is None:
            return None

        # Dedupe e-bill / challan that reprint the same invoice total
        for page_no, page_type, amount, _ in support:
            if amounts_equivalent(amount, chosen):
                result.warnings.append(
                    f"Page {page_no} ({page_type.value}) grand total {amount} matches bill; "
                    "treated as same document (not summed)"
                )
            else:
                result.warnings.append(
                    f"Page {page_no} ({page_type.value}) total {amount} differs from bill "
                    f"{chosen}; ignored for matching"
                )

        return chosen

    @staticmethod
    def _resolve_document_type(
        page_types: dict[int, DocumentType],
        result: ExtractionResult,
    ) -> DocumentType:
        types = set(page_types.values())
        if result.payment_rows or DocumentType.UTR_STATEMENT in types:
            return DocumentType.UTR_STATEMENT
        billish = types & {
            DocumentType.INVOICE,
            DocumentType.CASH_MEMO,
            DocumentType.RECEIPT,
            DocumentType.VOUCHER,
            DocumentType.BILL,
        }
        support = types & {DocumentType.EWAY_BILL, DocumentType.CHALLAN, DocumentType.GRN}
        if billish and (support or len(billish) > 1 or DocumentType.UNKNOWN in types):
            if DocumentType.INVOICE in billish:
                return DocumentType.INVOICE
            if DocumentType.CASH_MEMO in billish and DocumentType.GRN in types:
                return DocumentType.MIXED
            return next(iter(billish))
        if DocumentType.INVOICE in types:
            return DocumentType.INVOICE
        if DocumentType.CASH_MEMO in types and DocumentType.GRN in types:
            return DocumentType.MIXED
        if DocumentType.CASH_MEMO in types:
            return DocumentType.CASH_MEMO
        if DocumentType.GRN in types:
            return DocumentType.GRN
        if DocumentType.RECEIPT in types:
            return DocumentType.RECEIPT
        if DocumentType.VOUCHER in types:
            return DocumentType.VOUCHER
        if DocumentType.BILL in types:
            return DocumentType.BILL
        if DocumentType.EWAY_BILL in types:
            return DocumentType.EWAY_BILL
        if DocumentType.CHALLAN in types:
            return DocumentType.CHALLAN
        return result.document_type or DocumentType.UNKNOWN