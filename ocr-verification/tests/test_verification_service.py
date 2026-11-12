from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.models.document_models import DocumentType
from app.models.extraction_models import ExtractionResult, PaymentRow
from app.models.verification_models import VerificationDecision
from app.services.transaction_service import TransactionService
from app.services.transaction_verification_service import TransactionVerificationService


def _txn_payload(amount=85850, date="2026-11-07T00:00:00Z", narration="FOR WOOD LAKDA"):
    return {
        "_id": "txn-1",
        "date": date,
        "amount": amount,
        "narration": narration,
        "attachments": [
            {
                "name": "payment.pdf",
                "fileId": 101,
                "type": "OtherType",
            }
        ],
        "bankTransactionDetails": [
            {"payeeName": "JATOTH SHANKER", "UTRNo": "IN42631151879720"}
        ],
        "entryStatus": "Entry",
    }


@pytest.fixture
def service(tmp_path):
    from app.config import Settings

    settings = Settings(
        history_db_path=str(tmp_path / "history.db"),
        temp_dir=str(tmp_path / "temp"),
        api_auth_token=None,
    )
    txn = MagicMock()
    files = MagicMock()
    extraction = MagicMock()
    real_snapshot = TransactionService().to_snapshot
    txn.to_snapshot.side_effect = real_snapshot
    svc = TransactionVerificationService(
        settings=settings,
        transaction_service=txn,
        file_service=files,
        extraction_service=extraction,
    )
    return svc, txn, files, extraction


def _utr_extraction():
    return ExtractionResult(
        file_id="101",
        filename="payment.pdf",
        document_type=DocumentType.UTR_STATEMENT,
        page_count=1,
        extraction_status="SUCCESS",
        payment_rows=[
            PaymentRow(
                beneficiary_name="JATOTH SHANKER",
                amount=Decimal("85850.00"),
                payment_date="07-11-2026",
                remark="FOR WOOD LAKDA",
                utr_no="IN42631151879720",
                confidence=0.95,
            )
        ],
        fields={},
        metadata={"page_confidences": {"1": 1.0}},
    )


def test_successful_verification_updates_flag_only(service):
    svc, txn, files, extraction = service
    payload = _txn_payload()
    txn.get_transaction.return_value = payload
    files.download_file.return_value = MagicMock(
        file_id="101",
        filename="payment.pdf",
        local_path="dummy.pdf",
    )
    extraction.extract_file.return_value = _utr_extraction()
    txn.mark_verified_by_ocr.return_value = {"ok": True}

    result = svc.verify("txn-1")
    assert result.decision == VerificationDecision.VERIFIED
    assert result.status_updated is True
    txn.mark_verified_by_ocr.assert_called_once_with("txn-1", payload)


def test_amount_mismatch_does_not_update_status(service):
    svc, txn, files, extraction = service
    payload = _txn_payload(amount=100)
    txn.get_transaction.return_value = payload
    files.download_file.return_value = MagicMock(
        file_id="101", filename="payment.pdf", local_path="x.pdf"
    )
    extraction.extract_file.return_value = _utr_extraction()
    result = svc.verify("txn-1")
    assert result.decision in {
        VerificationDecision.MISMATCH,
        VerificationDecision.MANUAL_REVIEW,
    }
    assert result.status_updated is False
    txn.mark_verified_by_ocr.assert_not_called()


def test_missing_file_ids(service):
    svc, txn, files, extraction = service
    payload = _txn_payload()
    payload["attachments"] = []
    txn.get_transaction.return_value = payload
    result = svc.verify("txn-1")
    assert result.decision == VerificationDecision.DOCUMENT_NOT_FOUND
    files.download_file.assert_not_called()


def test_file_download_failure(service):
    svc, txn, files, extraction = service
    txn.get_transaction.return_value = _txn_payload()
    files.download_file.side_effect = FileNotFoundError("missing")
    result = svc.verify("txn-1")
    assert result.decision == VerificationDecision.DOCUMENT_NOT_FOUND
    assert result.status_updated is False


def test_duplicate_concurrent_request_blocked(service):
    svc, txn, files, extraction = service
    lock = svc._lock_for("txn-1")
    assert lock.acquire(blocking=False)
    try:
        result = svc.verify("txn-1")
        assert result.decision == VerificationDecision.MANUAL_REVIEW
        assert result.warnings
    finally:
        lock.release()