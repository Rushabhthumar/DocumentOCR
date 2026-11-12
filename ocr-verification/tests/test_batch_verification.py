from unittest.mock import MagicMock

from app.models.verification_models import BatchVerifyRequest, VerificationDecision, VerificationResult
from app.services.batch_verification_service import BatchVerificationService


def test_batch_skips_no_attachments_and_completes_verified(tmp_path):
    from app.config import Settings

    settings = Settings(
        history_db_path=str(tmp_path / "h.db"),
        temp_dir=str(tmp_path / "temp"),
        smtp_enabled=False,
        mail_to="rutvik.d@gurukul.org",
    )
    txn_service = MagicMock()
    verify_service = MagicMock()
    email_service = MagicMock()
    email_service.send_batch_summary.return_value = (False, "SMTP not configured")

    txn_service.list_transactions.return_value = [
        {"_id": "t1", "attachments": []},
        {
            "_id": "t2",
            "attachments": [{"fileId": 11643, "name": "a.pdf"}],
        },
        {
            "_id": "t3",
            "attachments": [{"fileId": 1, "name": "b.pdf"}, {"fileId": 2, "name": "c.pdf"}],
        },
    ]
    verify_service.verify.side_effect = [
        VerificationResult(
            transaction_id="t2",
            decision=VerificationDecision.VERIFIED,
            status_updated=True,
            message="ok",
        ),
        VerificationResult(
            transaction_id="t3",
            decision=VerificationDecision.MISMATCH,
            status_updated=False,
            message="date mismatch",
        ),
    ]

    service = BatchVerificationService(
        settings=settings,
        transaction_service=txn_service,
        verification_service=verify_service,
        email_service=email_service,
    )
    result = service.run(
        BatchVerifyRequest(
            companyId="comp1",
            modifiedDate="2026-11-01",
            periodId=79,
        )
    )

    assert result.total_fetched == 3
    assert result.skipped == 1
    assert result.processed == 2
    assert result.completed == 1
    assert result.failed == 1
    assert verify_service.verify.call_count == 2
    email_service.send_batch_summary.assert_called_once()


def test_normalize_list_shapes():
    from app.services.transaction_service import TransactionService

    assert len(TransactionService._normalize_transaction_list([{"_id": "1"}])) == 1
    assert len(TransactionService._normalize_transaction_list({"data": [{"_id": "1"}]})) == 1
    assert len(TransactionService._normalize_transaction_list({"items": [{"_id": "1"}]})) == 1
    assert TransactionService._normalize_transaction_list(None) == []