from __future__ import annotations

import copy
import logging
from decimal import Decimal
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings
from app.models.verification_models import TransactionSnapshot
from app.validators.amount_validator import normalize_amount

logger = logging.getLogger(__name__)


class TransactionService:
    def __init__(self, settings: Optional[Settings] = None, client: Optional[httpx.Client] = None):
        self.settings = settings or get_settings()
        self._client = client

    def _get_client(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(timeout=60.0, headers=self.settings.auth_headers())

    def get_transaction(self, transaction_id: str) -> dict[str, Any]:
        path = self.settings.transaction_get_path.format(transaction_id=transaction_id)
        url = f"{self.settings.finance_api_base_url.rstrip('/')}{path}"
        owns_client = self._client is None
        client = self._get_client()
        last_error: Exception | None = None
        try:
            # Finance occasionally returns an empty body under load; retry briefly.
            for attempt in range(1, 4):
                try:
                    response = client.get(url)
                    if response.status_code == 404:
                        raise LookupError(f"Transaction not found: {transaction_id}")
                    response.raise_for_status()
                    if not response.content or not response.content.strip():
                        raise ValueError(
                            f"Finance GetTransaction returned empty body "
                            f"(status={response.status_code}, attempt={attempt})"
                        )
                    return response.json()
                except LookupError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    logger.warning(
                        "GetTransaction %s attempt %s failed: %s",
                        transaction_id,
                        attempt,
                        exc,
                    )
                    if attempt < 3:
                        import time

                        time.sleep(0.4 * attempt)
            raise RuntimeError(
                f"Failed to fetch transaction {transaction_id}: {last_error}"
            ) from last_error
        finally:
            if owns_client:
                client.close()

    def list_transactions(
        self,
        *,
        company_id: str,
        period_id: int,
        modified_date: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        POST /Transactions/GetAll  (same as Angular getAllTransactions)
        Body: { companyId, periodId, modifiedDate? }
        Normalizes common list response shapes into a list of transaction dicts.
        """
        url = f"{self.settings.finance_api_base_url.rstrip('/')}{self.settings.transaction_list_path}"
        payload: dict[str, Any] = {
            "periodId": period_id,
            "companyId": company_id,
        }
        if modified_date:
            payload["modifiedDate"] = modified_date
        owns_client = self._client is None
        client = self._get_client()
        try:
            logger.info("Batch list POST %s payload=%s", url, payload)
            response = client.post(url, json=payload)
            logger.info("Batch list response status=%s", response.status_code)
            if response.status_code == 401:
                raise PermissionError(
                    "Finance GetAll returned 401 Unauthorized. "
                    "Pass Authorization (and AppId if required) header when calling /batch, "
                    "or set API_AUTH_TOKEN in .env for local Swagger debugging."
                )
            response.raise_for_status()
            data = response.json()
            items = self._normalize_transaction_list(data)
            logger.info("Batch list normalized count=%s", len(items))
            return items
        finally:
            if owns_client:
                client.close()

    @staticmethod
    def _normalize_transaction_list(data: Any) -> list[dict[str, Any]]:
        if data is None:
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("data", "items", "result", "results", "transactions", "value"):
                nested = data.get(key)
                if isinstance(nested, list):
                    return [item for item in nested if isinstance(item, dict)]
            # Single transaction object
            if data.get("_id") or data.get("id"):
                return [data]
        return []

    def to_snapshot(self, payload: dict[str, Any]) -> TransactionSnapshot:
        attachments = payload.get("attachments") or []
        file_ids: list[str] = []
        for item in attachments:
            if not isinstance(item, dict):
                continue
            file_id = item.get("fileId")
            if file_id is None or file_id == "" or file_id == 0:
                # Keep explicit zeros out unless they are real; 0 in sample schema is placeholder
                continue
            file_ids.append(str(file_id))

        bank_details = payload.get("bankTransactionDetails") or []
        payees = []
        utrs = []
        for bank in bank_details:
            if not isinstance(bank, dict):
                continue
            if bank.get("payeeName"):
                payees.append(str(bank["payeeName"]))
            if bank.get("UTRNo"):
                utrs.append(str(bank["UTRNo"]))

        party_name = payees[0] if payees else None
        amount = normalize_amount(payload.get("amount"))

        return TransactionSnapshot(
            transaction_id=str(payload.get("_id") or ""),
            date=payload.get("date"),
            amount=amount,
            narration=payload.get("narration"),
            party_name=party_name,
            entry_status=payload.get("entryStatus"),
            voucher_type=payload.get("voucherType"),
            instrument_no=payload.get("instrumentNo"),
            file_ids=file_ids,
            attachments=attachments,
            bank_payee_names=payees,
            utr_candidates=utrs,
            raw=payload,
        )

    def build_verified_update_payload(self, original_payload: dict[str, Any]) -> dict[str, Any]:
        """
        Use the exact GET transaction response, then append "OCR" to narration
        (without duplicating if already present). Does not set isTransactionVerifyByOCR.
        """
        payload = copy.deepcopy(original_payload)

        existing_narration = payload.get("narration")
        narration = "" if existing_narration is None else str(existing_narration)
        if "OCR" not in narration.upper().split():
            # Append marker; keep a clean separator
            if narration and not narration.endswith(("\n", " ", "\t")):
                narration = f"{narration} OCR"
            elif narration:
                narration = f"{narration}OCR"
            else:
                narration = "OCR"
            payload["narration"] = narration
        else:
            payload["narration"] = narration

        return payload

    def mark_verified_by_ocr(self, transaction_id: str, original_payload: dict[str, Any]) -> dict[str, Any]:
        """
        PUT the existing transaction GET response with OCR appended to narration.
        """
        payload = self.build_verified_update_payload(original_payload)

        # Keep a local copy of the exact PUT body for debugging/inspection
        try:
            debug_path = self.settings.temp_path / f"put_payload_{transaction_id}.json"
            import json

            debug_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            logger.info(
                "Prepared PUT %s narration=%r (saved %s)",
                transaction_id,
                payload.get("narration"),
                debug_path,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not save PUT payload debug file: %s", exc)

        path = self.settings.transaction_update_path.format(transaction_id=transaction_id)
        url = f"{self.settings.finance_api_base_url.rstrip('/')}{path}"
        owns_client = self._client is None
        client = self._get_client()
        try:
            response = client.put(url, json=payload)
            response.raise_for_status()
            if response.content:
                try:
                    return response.json()
                except Exception:  # noqa: BLE001
                    return {"ok": True, "requestPayload": payload}
            return {"ok": True, "requestPayload": payload}
        finally:
            if owns_client:
                client.close()