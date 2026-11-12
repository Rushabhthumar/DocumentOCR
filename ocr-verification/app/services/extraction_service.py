from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from app.config import Settings, get_settings
from app.models.document_models import DocumentType, PageExtractionMode
from app.models.extraction_models import ExtractionResult, FieldValue
from app.services.document_classifier import DocumentClassifier
from app.services.extractors import (
    extract_cash_memo_fields,
    extract_grn_fields,
    extract_payment_rows,
    payment_row_to_fields,
)
from app.services.image_service import ImageService
from app.services.ocr_service import OcrService
from app.services.pdf_service import PdfService

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
                            ocr_result = self.ocr_service.run_ocr(page.image)
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
                    ocr_result = self.ocr_service.run_ocr(image)
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

    def _populate_structured_fields(
        self,
        result: ExtractionResult,
        page_confidences: dict[int, float],
    ) -> None:
        page_types = result.page_types or {}
        # Mixed PDF: extract per page type and merge carefully
        merged_fields: dict[str, FieldValue] = {}
        for page_no, text in sorted(result.page_texts.items()):
            page_type = page_types.get(page_no, result.document_type)
            conf = page_confidences.get(page_no, 0.0)

            if page_type == DocumentType.UTR_STATEMENT or (
                result.document_type == DocumentType.UTR_STATEMENT and page_type == DocumentType.UNKNOWN
            ):
                rows = extract_payment_rows(text, page_number=page_no)
                result.payment_rows.extend(rows)
                if rows and not merged_fields:
                    # Default fields from first row; verification selects the matching row later
                    merged_fields.update(payment_row_to_fields(rows[0]))
                continue

            if page_type == DocumentType.CASH_MEMO:
                parsed = extract_cash_memo_fields(text, page_number=page_no, confidence=conf)
                for key, value in parsed["fields"].items():
                    # Prefer cash memo amount/date over GRN blanks
                    if key not in merged_fields or merged_fields[key].value is None:
                        merged_fields[key] = value
                    elif key in {"amount", "documentDate", "documentNumber", "partyName"} and value.value:
                        merged_fields[key] = value
                result.items.extend(parsed["items"])
                result.warnings.extend(parsed["warnings"])
                continue

            if page_type == DocumentType.GRN:
                parsed = extract_grn_fields(text, page_number=page_no, confidence=conf)
                for key, value in parsed["fields"].items():
                    if key == "amount":
                        # Never overwrite a real amount with GRN null amount
                        if "amount" not in merged_fields:
                            merged_fields[key] = value
                        continue
                    existing = merged_fields.get(key)
                    # Do not replace an already extracted non-null cash-memo field with GRN blanks/noise
                    if existing and existing.value is not None:
                        continue
                    if value.value is None:
                        if key not in merged_fields:
                            merged_fields[key] = value
                        continue
                    merged_fields[key] = value
                result.items.extend(parsed["items"])
                result.warnings.extend(parsed["warnings"])
                continue

        if result.document_type == DocumentType.UTR_STATEMENT and result.payment_rows:
            result.document_type = DocumentType.UTR_STATEMENT
        elif DocumentType.CASH_MEMO in page_types.values() and DocumentType.GRN in page_types.values():
            result.document_type = DocumentType.MIXED
        elif DocumentType.CASH_MEMO in page_types.values():
            result.document_type = DocumentType.CASH_MEMO
        elif DocumentType.GRN in page_types.values():
            result.document_type = DocumentType.GRN

        result.fields = merged_fields or {
            "documentNumber": FieldValue(value=None, confidence=0, reason="not extracted"),
            "documentDate": FieldValue(value=None, confidence=0, reason="not extracted"),
            "partyName": FieldValue(value=None, confidence=0, reason="not extracted"),
            "amount": FieldValue(value=None, confidence=0, reason="not extracted"),
            "description": FieldValue(value=None, confidence=0, reason="not extracted"),
        }