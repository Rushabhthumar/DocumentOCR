from app.services.transaction_service import TransactionService


def test_build_verified_update_payload_appends_ocr_to_narration():
    service = TransactionService()
    original = {
        "_id": "txn-1",
        "amount": 100,
        "narration": "sdf\n",
        "isTransactionVerifyByOCR": False,
    }
    payload = service.build_verified_update_payload(original)
    # Flag is left unchanged for now; only narration is updated
    assert payload["isTransactionVerifyByOCR"] is False
    assert payload["narration"].strip().endswith("OCR")
    assert "sdf" in payload["narration"]


def test_build_verified_update_payload_does_not_duplicate_ocr():
    service = TransactionService()
    original = {
        "_id": "txn-1",
        "narration": "sdf OCR",
        "isTransactionVerifyByOCR": False,
    }
    payload = service.build_verified_update_payload(original)
    assert payload["narration"].count("OCR") == 1
    assert payload["isTransactionVerifyByOCR"] is False
