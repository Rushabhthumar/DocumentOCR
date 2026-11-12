from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    CASH_MEMO = "CashMemo"
    GRN = "GoodsReceivedNote"
    UTR_STATEMENT = "UtrPaymentStatement"
    INVOICE = "Invoice"
    UNKNOWN = "Unknown"
    MIXED = "Mixed"


class PageExtractionMode(str, Enum):
    TEXT = "TEXT"
    OCR = "OCR"
    FAILED = "FAILED"


class PageContent(BaseModel):
    page_number: int
    mode: PageExtractionMode
    text: str = ""
    confidence: Optional[float] = None
    warnings: list[str] = Field(default_factory=list)
    width: Optional[float] = None
    height: Optional[float] = None


class DocumentBinary(BaseModel):
    file_id: str
    filename: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: int
    local_path: str


class ClassifiedPage(BaseModel):
    page_number: int
    document_type: DocumentType
    confidence: float = 0.0
    signals: list[str] = Field(default_factory=list)


class ClassificationResult(BaseModel):
    primary_type: DocumentType
    pages: list[ClassifiedPage] = Field(default_factory=list)
    confidence: float = 0.0
    raw_signals: dict[str, Any] = Field(default_factory=dict)