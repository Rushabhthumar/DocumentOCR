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

    file_api_base_url: str = "https://files.api.gurukul.ninja"
    file_download_path: str = "/File/DownloadOCR/{file_id}"

    api_auth_header: str = "Authorization"
    api_auth_token: Optional[str] = None

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
    enable_trocr_fallback: bool = False

    history_db_path: str = "./temp/verification_history.db"
    temp_dir: str = "./temp"

    # Status field written only on successful verification
    verified_flag_field: str = "isTransactionVerifyByOCR"
    verified_flag_value: bool = True

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
    def history_path(self) -> Path:
        path = Path(self.history_db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def auth_headers(self) -> dict[str, str]:
        if not self.api_auth_token:
            return {}
        return {self.api_auth_header: self.api_auth_token}


@lru_cache
def get_settings() -> Settings:
    return Settings()