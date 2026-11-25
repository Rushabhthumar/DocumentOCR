from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "OCR Transaction Verification"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = True

    finance_api_base_url: str = "https://finance.api.gurukul.ninja"
    transaction_get_path: str = "/Transactions/GetTransaction/{transaction_id}"
    transaction_update_path: str = "/Transactions/{transaction_id}?isMigration=false"
    # List endpoint used by Angular getAllTransactions: POST /Transactions/GetAll
    transaction_list_path: str = "/Transactions/GetAll"

    file_api_base_url: str = "https://files.api.gurukul.ninja"
    file_download_path: str = "/File/DownloadOCR/{file_id}"

    api_auth_header: str = "Authorization"
    api_auth_token: Optional[str] = None
    # Static client key used by Angular for Files API (also accept per-request Apikey)
    file_api_key: Optional[str] = None
    # Optional fallback AppId when Angular does not forward it
    file_app_id: Optional[str] = None

    amount_tolerance: str = "0.01"
    date_tolerance_days: int = 0
    party_fuzzy_threshold: int = 85
    min_ocr_confidence: float = 0.55
    max_pdf_pages: int = 30
    max_file_size_mb: int = 25
    # Description matching disabled for now; enable later when ready
    enable_description_check: bool = False

    ocr_lang: str = "en"
    use_gpu: bool = False
    enable_structure_ocr: bool = True
    enable_trocr_fallback: bool = True
    # Structure OCR is heavy on CPU; use only when page looks tabular/invoice-like
    structure_ocr_for_all_pages: bool = False

    history_db_path: str = "./temp/verification_history.db"
    temp_dir: str = "./temp"
    # Downloaded docs for failed verifications (review / retrain later)
    failed_verification_dir: str = "./FailedVarificationDocuments"

    # Status field written only on successful verification
    verified_flag_field: str = "isTransactionVerifyByOCR"
    verified_flag_value: bool = True
    # When false: compare OCR vs transaction but do NOT PUT narration / verified flag
    # Use this for the first pass against production data.
    update_on_verify: bool = True

    # Temporary test filter: if set, batch processes only this transaction _id
    batch_test_transaction_id: Optional[str] = None

    # --- RabbitMQ batch (off by default — sync path unchanged) ---
    batch_use_rabbitmq: bool = False
    rabbitmq_host: str = "10.10.0.88"
    rabbitmq_port: int = 5672
    rabbitmq_username: str = "gurukulUser"
    rabbitmq_password: str = "gurukulUser"
    rabbitmq_virtual_host: str = "vrHost"
    # Same shape as .NET Queue:InsertTxQueue / FailedInsertTxQueue / DeadExchange
    rabbitmq_queue_verify: str = "ocr.VerifyTxQueue"
    rabbitmq_queue_failed: str = "ocr.FailedVerifyTxQueue"
    rabbitmq_dead_exchange: str = "ocr.VerifyTxDeadExchange"
    rabbitmq_worker_count: int = 4
    rabbitmq_prefetch: int = 1

    # Connect / DNS retry before treating download as failed
    network_retry_max_attempts: int = 5
    network_retry_base_delay_seconds: float = 2.0
    network_retry_max_delay_seconds: float = 60.0

    # Token renew (URL filled when you share the endpoint)
    token_renew_api_url: Optional[str] = None
    token_renew_api_method: str = "POST"
    token_renew_interval_seconds: int = 3000  # 50 minutes
    # JSON path to token in renew response, e.g. "accessToken" or "data.token"
    token_renew_response_token_path: str = "accessToken"

    # Batch summary email via free Gmail SMTP only (no paid email APIs)
    mail_to: str = "rutvik.d@gurukul.org"
    mail_from: Optional[str] = None  # defaults to SMTP_USERNAME (Gmail address)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: Optional[str] = None  # your free Gmail address
    smtp_password: Optional[str] = None  # Gmail App Password (not account password)
    smtp_use_tls: bool = True
    smtp_enabled: bool = False

    @property
    def amount_tolerance_decimal(self):
        from decimal import Decimal

        return Decimal(self.amount_tolerance)

    @property
    def temp_path(self) -> Path:
        path = Path(self.temp_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def failed_verification_path(self) -> Path:
        path = Path(self.failed_verification_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def history_path(self) -> Path:
        path = Path(self.history_db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def auth_headers(self, *, for_files: bool = False) -> dict[str, str]:
        """
        Prefer request ContextVar (HTTP), then process AuthTokenStore (workers),
        then .env. Apikey is Files-API only.
        """
        from app.auth_context import (
            get_request_api_key,
            get_request_app_id,
            get_request_authorization,
        )
        from app.services.auth_token_store import auth_token_store

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        request_auth = get_request_authorization()
        token = request_auth or auth_token_store.get_authorization() or self.api_auth_token
        if token:
            token = token.strip()
            if token and not token.lower().startswith("bearer "):
                token = f"Bearer {token}"
            headers[self.api_auth_header] = token

        app_id = get_request_app_id() or auth_token_store.get_app_id() or self.file_app_id
        if app_id:
            headers["AppId"] = app_id
            if for_files:
                # Angular Files calls use Appid casing
                headers["Appid"] = app_id

        if for_files:
            api_key = (
                get_request_api_key()
                or auth_token_store.get_api_key()
                or self.file_api_key
            )
            if api_key:
                headers["Apikey"] = api_key

        return headers


@lru_cache
def get_settings() -> Settings:
    return Settings()