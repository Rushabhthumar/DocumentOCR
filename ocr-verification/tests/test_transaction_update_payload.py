from app.services.transaction_service import TransactionService


def test_build_verified_update_payload_sets_is_ocr_verified():
    service = TransactionService()
    original = {
        "_id": "txn-1",
        "amount": 100,
        "narration": "PAID TO VENDOR FOR SUPPLY",
        "isOCRVerified": False,
    }
    payload = service.build_verified_update_payload(original)
    assert payload["isOCRVerified"] is True
    # Narration must not be changed
    assert payload["narration"] == "PAID TO VENDOR FOR SUPPLY"


def test_build_ocr_flag_update_payload_can_set_false():
    service = TransactionService()
    original = {
        "_id": "txn-1",
        "narration": "some text",
        "isOCRVerified": True,
    }
    payload = service.build_ocr_flag_update_payload(original, is_ocr_verified=False)
    assert payload["isOCRVerified"] is False
    assert payload["narration"] == "some text"
