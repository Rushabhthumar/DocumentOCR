from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app import EXTRACTOR_BUILD, __version__
from app.api.ocr_controller import router as ocr_router
from app.api.verification_controller import router as verification_router
from app.auth_context import clear_request_auth, set_request_auth
from app.config import get_settings
from app.services.auth_token_store import auth_token_store
from app.services.history_service import HistoryService
from app.services.rabbitmq.consumer import rabbitmq_worker_manager
from app.services.token_renew_scheduler import token_renew_scheduler


class ForwardAuthMiddleware(BaseHTTPMiddleware):
    """Capture Angular Authorization / AppId / Apikey for Finance/File API calls."""

    async def dispatch(self, request: Request, call_next):
        authorization = request.headers.get("authorization") or request.headers.get("Authorization")
        app_id = (
            request.headers.get("appid")
            or request.headers.get("AppId")
            or request.headers.get("Appid")
        )
        api_key = (
            request.headers.get("apikey")
            or request.headers.get("ApiKey")
            or request.headers.get("Apikey")
        )
        set_request_auth(authorization, app_id, api_key)
        # Keep process-wide store fresh for RabbitMQ workers
        if authorization or app_id or api_key:
            auth_token_store.update(
                authorization=authorization,
                app_id=app_id,
                api_key=api_key,
            )
        try:
            return await call_next(request)
        finally:
            clear_request_auth()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    settings.temp_path.mkdir(parents=True, exist_ok=True)
    HistoryService(settings)
    if settings.api_auth_token:
        auth_token_store.update(authorization=settings.api_auth_token)
    if settings.file_app_id:
        auth_token_store.update(app_id=settings.file_app_id)
    if settings.file_api_key:
        auth_token_store.update(api_key=settings.file_api_key)
    logger.info(
        "Started %s v%s (auth %s) rabbitmq=%s workers=%s",
        settings.app_name,
        __version__,
        "enabled" if settings.api_auth_token else "disabled/dev",
        settings.batch_use_rabbitmq,
        settings.rabbitmq_worker_count if settings.batch_use_rabbitmq else 0,
    )
    rabbitmq_worker_manager.start()
    token_renew_scheduler.start()
    try:
        yield
    finally:
        token_renew_scheduler.stop()
        rabbitmq_worker_manager.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(ForwardAuthMiddleware)
    app.include_router(verification_router)
    app.include_router(ocr_router)

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "version": __version__,
            "extractorBuild": EXTRACTOR_BUILD,
            "authConfigured": bool(settings.api_auth_token),
            "batchUseRabbitMq": settings.batch_use_rabbitmq,
            "rabbitMqWorkers": settings.rabbitmq_worker_count if settings.batch_use_rabbitmq else 0,
            "tokenRenewConfigured": bool(settings.token_renew_api_url),
        }

    return app


app = create_app()