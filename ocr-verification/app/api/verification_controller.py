from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.models.verification_models import (
    BatchJobStatusResponse,
    BatchVerifyRequest,
    VerifyRequest,
)
from app.services.batch_job_tracker import batch_job_tracker
from app.services.batch_pause_controller import batch_pause_controller
from app.services.batch_verification_service import BatchVerificationService
from app.services.history_service import HistoryService
from app.services.transaction_verification_service import TransactionVerificationService

router = APIRouter(prefix="/api/transaction-verification", tags=["Transaction Verification"])


@router.post("/batch")
def verify_transactions_batch(body: BatchVerifyRequest):
    """
    Fetch transactions by companyId + periodId (modifiedDate ignored for GetAll), then verify each
    transaction that has attachments.

    When BATCH_USE_RABBITMQ=false (default): sync in-process loop (unchanged).
    When BATCH_USE_RABBITMQ=true: enqueue jobs and return batch_job_id for polling.
    """
    service = BatchVerificationService()
    result = service.run(body)
    return result.model_dump(mode="json")


@router.get("/batch/jobs/{job_id}")
def get_batch_job_status(job_id: str):
    job = batch_job_tracker.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Batch job not found")
    return BatchJobStatusResponse(
        job_id=job["job_id"],
        company_id=job.get("company_id"),
        period_id=job.get("period_id"),
        status=job["status"],
        total_queued=job.get("total_queued") or 0,
        completed=job.get("completed") or 0,
        failed=job.get("failed") or 0,
        skipped=job.get("skipped") or 0,
        pending=job.get("pending") or 0,
        message=job.get("message") or "",
        created_at=job.get("created_at"),
        updated_at=job.get("updated_at"),
        items=job.get("items") or [],
    ).model_dump(mode="json")


@router.get("/batch/pause-status")
def get_batch_pause_status():
    return {
        "paused": batch_pause_controller.is_paused,
        "reason": batch_pause_controller.reason,
    }


@router.post("/{transaction_id}/verify")
def verify_transaction(transaction_id: str, body: VerifyRequest | None = None):
    """
    Verify a single transaction by ID.
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