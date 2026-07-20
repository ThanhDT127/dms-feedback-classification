"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from ..settings import SERVICE_DIR
from .api.auth_api import router as auth_router
from .api.auth_api import user_router
from .api.classify import router as classify_router
from .api.files import router as files_router
from .api.metrics_api import router as metrics_router
from .api.pipeline_api import router as pipeline_router
from .api.settings_api import router as settings_router
from .rate_limit import limiter
from .ws.logs import router as ws_logs_router
from .ws.progress import router as ws_progress_router

logger = logging.getLogger("dms-web")

STATIC_DIR = SERVICE_DIR / "static"


async def _token_blacklist_cleanup_loop() -> None:
    from ..token_blacklist import cleanup

    while True:
        cleanup()
        await asyncio.sleep(300)


async def _rate_limit_handler(request: StarletteRequest, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Try again later."},
        headers={"Retry-After": "60"},
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self' wss: ws:"
        )
        # Add HSTS only outside development
        env = os.environ.get("ENVIRONMENT", "development").lower()
        if env != "development":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


def _ensure_runtime_dirs() -> None:
    work_dir = SERVICE_DIR / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "input").mkdir(parents=True, exist_ok=True)
    (work_dir / "output").mkdir(parents=True, exist_ok=True)
    (work_dir / "checkpoint").mkdir(parents=True, exist_ok=True)
    log_dir = SERVICE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)


def _restore_state_from_sharepoint_sync() -> None:
    import json

    from . import deps

    settings = deps.get_settings()
    sp_client = deps.get_sharepoint_client()
    if not settings or not sp_client:
        return

    seen_missing = True
    if settings.seen_files_path.exists():
        try:
            seen_data = json.loads(settings.seen_files_path.read_text(encoding="utf-8"))
            if seen_data and len(seen_data) > 0:
                seen_missing = False
        except Exception:
            pass

    metrics_missing = True
    if settings.metrics_path.exists():
        try:
            metrics_data = json.loads(settings.metrics_path.read_text(encoding="utf-8"))
            if metrics_data and metrics_data.get("files_processed", 0) > 0:
                metrics_missing = False
        except Exception:
            pass

    if not seen_missing and not metrics_missing:
        return

    logger.info(
        "Web server detected missing/empty local state (seen_missing: %s, metrics_missing: %s). Restoring from SharePoint Check_Point/...",
        seen_missing,
        metrics_missing,
    )
    ckpt_items = sp_client.list_folder_items(settings.sp_checkpoint_folder)
    for item in ckpt_items:
        name = item.get("name")
        file_id = item.get("id")
        if name == "seen_files.json" and seen_missing:
            logger.info("Web server: Restoring seen_files.json...")
            sp_client.download_file(file_id, settings.seen_files_path)
            logger.info("Web server: Restoring seen_files.json complete")
        elif name == "metrics.json" and metrics_missing:
            logger.info("Web server: Restoring metrics.json...")
            sp_client.download_file(file_id, settings.metrics_path)
            logger.info("Web server: Restoring metrics.json complete")

    metrics_collector = deps.get_metrics()
    if metrics_collector:
        metrics_collector._load()


async def _restore_state_from_sharepoint(timeout_seconds: float = 30.0) -> None:
    try:
        await asyncio.wait_for(
            asyncio.to_thread(_restore_state_from_sharepoint_sync),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        logger.warning(
            "Web server state restore from SharePoint timed out after %.1fs; continuing startup",
            timeout_seconds,
        )
    except Exception as exc:
        logger.warning("Web server failed to restore state from SharePoint Check_Point/: %s", exc)


def _start_classification_worker() -> None:
    from . import deps

    worker_manager = deps.get_classification_worker_manager()
    if worker_manager is not None:
        worker_manager.start()


def _stop_classification_worker() -> None:
    from . import deps

    worker_manager = deps.get_classification_worker_manager()
    if worker_manager is not None:
        worker_manager.stop()


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("DMS Web UI đang khởi động...")
        app.state.token_blacklist_cleanup_task = asyncio.create_task(
            _token_blacklist_cleanup_loop()
        )
        _ensure_runtime_dirs()
        await _restore_state_from_sharepoint()
        try:
            await asyncio.to_thread(_start_classification_worker)
        except Exception as exc:
            logger.warning("Classification worker could not start: %s", exc)

        logger.info("DMS Web UI sẵn sàng tại http://0.0.0.0:8501")
        try:
            yield
        finally:
            cleanup_task = getattr(app.state, "token_blacklist_cleanup_task", None)
            if cleanup_task is not None:
                cleanup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await cleanup_task

            try:
                await asyncio.to_thread(_stop_classification_worker)
            except Exception as exc:
                logger.warning("Classification worker shutdown failed: %s", exc)

    app = FastAPI(
        title="DMS PhÃ¢n loáº¡i pháº£n há»“i",
        description="Giao diá»‡n quáº£n lÃ½ há»‡ thá»‘ng phÃ¢n loáº¡i pháº£n há»“i DMS Ráº¡ng ÄÃ´ng",
        version="1.0.0",
        lifespan=lifespan,
    )

    # --- Security Headers ---
    app.add_middleware(SecurityHeadersMiddleware)

    # --- CORS ---
    # Configure via CORS_ALLOWED_ORIGINS env var (comma-separated).
    # Defaults to localhost origins. Set explicit origins in production.
    cors_origins_raw = os.environ.get(
        "CORS_ALLOWED_ORIGINS", "http://localhost:8501,http://localhost:3000"
    )
    if cors_origins_raw == "*":
        env = os.environ.get("ENVIRONMENT", "development").lower()
        if env != "development":
            logger.warning("CORS wildcard '*' is not recommended outside development")
    cors_origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=cors_origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Rate Limiting ---
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

    # --- API routers ---
    app.include_router(auth_router)
    app.include_router(user_router)
    app.include_router(metrics_router)
    app.include_router(files_router)
    app.include_router(classify_router)
    app.include_router(settings_router)
    app.include_router(pipeline_router)

    # --- WebSocket routers ---
    app.include_router(ws_progress_router)
    app.include_router(ws_logs_router)

    # --- Root serves index.html ---
    @app.get("/", include_in_schema=False)
    async def serve_index():
        index_path = STATIC_DIR / "index.html"
        if index_path.is_file():
            return FileResponse(str(index_path), media_type="text/html")
        return {"message": "DMS PhÃ¢n loáº¡i pháº£n há»“i - API Ä‘ang hoáº¡t Ä‘á»™ng"}

    # --- Static files (separate mounts for css/js to avoid overriding API) ---
    if STATIC_DIR.is_dir():
        css_dir = STATIC_DIR / "css"
        js_dir = STATIC_DIR / "js"
        if css_dir.is_dir():
            app.mount("/css", StaticFiles(directory=str(css_dir)), name="css")
        if js_dir.is_dir():
            app.mount("/js", StaticFiles(directory=str(js_dir)), name="js")
        assets_dir = STATIC_DIR / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    return app
