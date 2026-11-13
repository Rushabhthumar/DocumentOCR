from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.extraction_models import ExtractionResult


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class VerificationDecision(str, Enum):
    VERIFIED = "VERIFIED"
    MISMATCH = "MISMATCH"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"


class FieldComparisonResult(BaseModel):
    field_name: str
    expected: Optional[Any] = None
    extracted: Optional[Any] = None
    matched: bool = False
    score: Optional[float] = None
    difference: Optional[Any] = None
    status: str = "COMPARED"
    explanation: str = ""


class DocumentVerificationResult(BaseModel):
    file_id: str
    filename: Optional[str] = None
    document_type: Optional[str] = None
    extraction: Optional[ExtractionResult] = None
    field_comparisons: list[FieldComparisonResult] = Field(default_factory=list)
    decision: VerificationDecision = VerificationDecision.MANUAL_REVIEW
    warnings: list[str] = Field(default_factory=list)


class TransactionSnapshot(BaseModel):
    transaction_id: str
    date: Optional[str] = None
    amount: Optional[Decimal] = None
    narration: Optional[str] = None
    party_name: Optional[str] = None
    entry_status: Optional[str] = None
    voucher_type: Optional[str] = None
    instrument_no: Optional[str] = None
    file_ids: list[str] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    bank_payee_names: list[str] = Field(default_factory=list)
    utr_candidates: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class VerificationResult(BaseModel):
    transaction_id: str
    decision: VerificationDecision
    status_updated: bool = False
    verified_flag_set: bool = False
    timestamp: datetime = Field(default_factory=utc_now)
    transaction: Optional[TransactionSnapshot] = None
    documents: list[DocumentVerificationResult] = Field(default_factory=list)
    field_comparisons: list[FieldComparisonResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    message: str = ""


class VerifyRequest(BaseModel):
    """Angular should send only transaction id via path; body is optional."""

    force: bool = False


class LocalExtractRequest(BaseModel):
    document_hint: Optional[str] = None


class BatchVerifyRequest(BaseModel):
    companyId: str
    periodId: int
    modifiedDate: Optional[str] = None  # ISO date/datetime; omit to fetch all for period
    force: bool = True


class BatchTransactionOutcome(BaseModel):
    transaction_id: str
    status: str  # COMPLETED | FAILED | SKIPPED
    decision: Optional[str] = None
    status_updated: bool = False
    file_ids: list[str] = Field(default_factory=list)
    message: str = ""
    errors: list[str] = Field(default_factory=list)


class BatchVerifyResponse(BaseModel):
    companyId: str
    periodId: int
    modifiedDate: Optional[str] = None
    total_fetched: int = 0
    processed: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    email_sent: bool = False
    email_to: Optional[str] = None
    email_error: Optional[str] = None
    results: list[BatchTransactionOutcome] = Field(default_factory=list)
    message: str = ""