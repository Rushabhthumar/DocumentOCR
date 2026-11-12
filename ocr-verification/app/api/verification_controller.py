from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.models.verification_models import VerifyRequest
from app.services.history_service import HistoryService
from app.services.transaction_verification_service import TransactionVerificationService

router = APIRouter(prefix="/api/transaction-verification", tags=["Transaction Verification"])


@router.post("/{transaction_id}/verify")
def verify_transaction(transaction_id: str, body: VerifyRequest | None = None):
    """
    Angular should send only the transaction ID.
    Backend loads authoritative transaction + file IDs from Finance API.
    """
    force = body.force if body else False
    service = TransactionVerificationService()
    result = service.verify(transaction_id, force=force)
    return result.model_dump(mode="json")


@router.get("/{transaction_id}/result")
def get_verification_result(transaction_id: str):
    history = HistoryService()
    latest = history.latest(transaction_id)
    if not latest:
        raise HTTPException(status_code=404, detail="No verification result found")
    return latest


@router.get("/{transaction_id}/history")
def get_verification_history(transaction_id: str, limit: int = Query(50, ge=1, le=200)):
    history = HistoryService()
    return history.history(transaction_id, limit=limit)