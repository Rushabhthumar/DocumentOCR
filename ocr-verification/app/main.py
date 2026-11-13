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
from app.services.history_service import HistoryService


class ForwardAuthMiddleware(BaseHTTPMiddleware):
    """Capture Angular Authorization / AppId and use them for Finance/File API calls."""

    async def dispatch(self, request: Request, call_next):
        authorization = request.headers.get("authorization") or request.headers.get("Authorization")
        app_id = request.headers.get("appid") or request.headers.get("AppId")
        set_request_auth(authorization, app_id)
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
    logger.info(
        "Started %s v%s (auth %s)",
        settings.app_name,
        __version__,
        "enabled" if settings.api_auth_token else "disabled/dev",
    )
    yield


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
        }

    return app


app = create_app()