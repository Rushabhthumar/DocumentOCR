from __future__ import annotations

import logging
from typing import Optional

from app.config import Settings, get_settings
from app.models.verification_models import (
    BatchTransactionOutcome,
    BatchVerifyRequest,
    BatchVerifyResponse,
    VerificationDecision,
)
from app.services.email_service import EmailService
from app.services.transaction_service import TransactionService
from app.services.transaction_verification_service import TransactionVerificationService

logger = logging.getLogger(__name__)


class BatchVerificationService:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        transaction_service: Optional[TransactionService] = None,
        verification_service: Optional[TransactionVerificationService] = None,
        email_service: Optional[EmailService] = None,
    ):
        self.settings = settings or get_settings()
        self.transaction_service = transaction_service or TransactionService(self.settings)
        self.verification_service = verification_service or TransactionVerificationService(
            self.settings
        )
        self.email_service = email_service or EmailService(self.settings)

    @staticmethod
    def _transaction_id(item: dict) -> str:
        return str(item.get("_id") or item.get("id") or "")

    @staticmethod
    def _attachment_file_ids(item: dict) -> list[str]:
        attachments = item.get("attachments") or []
        if not isinstance(attachments, list):
            return []
        file_ids: list[str] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            file_id = attachment.get("fileId")
            if file_id is None or file_id == "" or file_id == 0:
                continue
            file_ids.append(str(file_id))
        return file_ids

    def run(self, request: BatchVerifyRequest) -> BatchVerifyResponse:
        response = BatchVerifyResponse(
            companyId=request.companyId,
            periodId=request.periodId,
            modifiedDate=request.modifiedDate,
            email_to=self.settings.mail_to,
        )

        try:
            logger.info(
                "Starting batch verify companyId=%s periodId=%s modifiedDate=%s",
                request.companyId,
                request.periodId,
                request.modifiedDate,
            )
            transactions = self.transaction_service.list_transactions(
                company_id=request.companyId,
                period_id=request.periodId,
                modified_date=request.modifiedDate or None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Batch fetch failed")
            response.message = f"Failed to fetch transactions: {exc}"
            response.email_sent, response.email_error = self._send_summary(response)
            return response

        response.total_fetched = len(transactions)
        if not transactions:
            response.message = "No transactions returned for the given filters"
            response.email_sent, response.email_error = self._send_summary(response)
            return response

        test_txn_id = (self.settings.batch_test_transaction_id or "").strip()
        if test_txn_id:
            before = len(transactions)
            transactions = [
                item
                for item in transactions
                if self._transaction_id(item) == test_txn_id
            ]
            logger.info(
                "TEST FILTER: batch limited to transaction_id=%s (%s of %s fetched)",
                test_txn_id,
                len(transactions),
                before,
            )
            if not transactions:
                response.message = (
                    f"TEST FILTER: transaction {test_txn_id} not found in fetched list "
                    f"(fetched={before})"
                )
                response.email_sent, response.email_error = self._send_summary(response)
                return response

        for item in transactions:
            txn_id = self._transaction_id(item)
            file_ids = self._attachment_file_ids(item)

            if not txn_id:
                response.skipped += 1
                response.results.append(
                    BatchTransactionOutcome(
                        transaction_id="",
                        status="SKIPPED",
                        message="Transaction missing _id",
                    )
                )
                continue

            if not file_ids:
                response.skipped += 1
                response.results.append(
                    BatchTransactionOutcome(
                        transaction_id=txn_id,
                        status="SKIPPED",
                        message="No attachments/fileIds on transaction",
                        file_ids=[],
                    )
                )
                continue

            response.processed += 1
            try:
                # Reuse single-txn pipeline: authoritative GET + all file OCR + match + conditional PUT
                result = self.verification_service.verify(txn_id, force=request.force)
                if (
                    result.decision == VerificationDecision.VERIFIED
                    and result.status_updated
                ):
                    response.completed += 1
                    response.results.append(
                        BatchTransactionOutcome(
                            transaction_id=txn_id,
                            status="COMPLETED",
                            decision=result.decision.value,
                            status_updated=True,
                            file_ids=file_ids,
                            message=result.message,
                        )
                    )
                else:
                    response.failed += 1
                    failure_errors = list(result.errors or [])
                    if not failure_errors and result.message:
                        failure_errors = [result.message]
                    response.results.append(
                        BatchTransactionOutcome(
                            transaction_id=txn_id,
                            status="FAILED",
                            decision=result.decision.value,
                            status_updated=False,
                            file_ids=file_ids,
                            message=result.message or f"Decision {result.decision.value}",
                            errors=failure_errors,
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Batch verification failed for %s", txn_id)
                response.failed += 1
                response.results.append(
                    BatchTransactionOutcome(
                        transaction_id=txn_id,
                        status="FAILED",
                        decision=VerificationDecision.EXTRACTION_FAILED.value,
                        status_updated=False,
                        file_ids=file_ids,
                        message="Skipped PUT due to processing error",
                        errors=[str(exc)],
                    )
                )

        response.message = (
            f"Batch complete. fetched={response.total_fetched}, "
            f"processed={response.processed}, completed={response.completed}, "
            f"failed={response.failed}, skipped={response.skipped}"
        )
        response.email_sent, response.email_error = self._send_summary(response)
        if not response.email_sent and response.email_error:
            response.message = f"{response.message}. {response.email_error}"
        return response

    def _send_summary(self, response: BatchVerifyResponse) -> tuple[bool, Optional[str]]:
        subject = (
            f"OCR Batch Verification Summary | company={response.companyId} "
            f"| period={response.periodId}"
        )
        lines = [
            "OCR Transaction Batch Verification Summary",
            "",
            f"Company ID: {response.companyId}",
            f"Period ID: {response.periodId}",
            f"Modified Date: {response.modifiedDate or '(all)'}",
            "",
            f"Total fetched: {response.total_fetched}",
            f"Processed (with attachments): {response.processed}",
            f"Completed (VERIFIED + updated): {response.completed}",
            f"Failed / not updated: {response.failed}",
            f"Skipped (no attachments / invalid): {response.skipped}",
            "",
            "Details:",
        ]
        for item in response.results:
            lines.append(
                f"- {item.transaction_id or '(missing id)'} | {item.status} | "
                f"decision={item.decision or '-'} | files={','.join(item.file_ids) or '-'} | "
                f"{item.message}"
            )
            for err in item.errors:
                lines.append(f"    error: {err}")

        body = "\n".join(lines)
        return self.email_service.send_batch_summary(subject=subject, body=body)