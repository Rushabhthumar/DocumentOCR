from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.document_models import DocumentType


class FieldValue(BaseModel):
    value: Optional[Any] = None
    confidence: float = 0.0
    page_number: Optional[int] = None
    source: Optional[str] = None
    reason: Optional[str] = None


class LineItem(BaseModel):
    description: Optional[str] = None
    quantity: Optional[str] = None
    unit_price: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    confidence: float = 0.0
    page_number: Optional[int] = None


class PaymentRow(BaseModel):
    payment_mode: Optional[str] = None
    debit_account_no: Optional[str] = None
    beneficiary_name: Optional[str] = None
    beneficiary_account_no: Optional[str] = None
    bene_ifsc_code: Optional[str] = None
    amount: Optional[Decimal] = None
    payment_date: Optional[str] = None
    remark: Optional[str] = None
    status: Optional[str] = None
    utr_no: Optional[str] = None
    confidence: float = 0.0
    page_number: int = 1
    source_lines: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    file_id: str
    filename: Optional[str] = None
    document_type: DocumentType = DocumentType.UNKNOWN
    page_count: int = 0
    extraction_status: str = "SUCCESS"
    fields: dict[str, FieldValue] = Field(default_factory=dict)
    items: list[LineItem] = Field(default_factory=list)
    payment_rows: list[PaymentRow] = Field(default_factory=list)
    page_types: dict[int, DocumentType] = Field(default_factory=dict)
    raw_text: str = ""
    page_texts: dict[int, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)