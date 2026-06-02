"""FastAPI application factory."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..settings import SERVICE_DIR
from .api.classify import router as classify_router
from .api.files import router as files_router
from .api.metrics_api import router as metrics_router
from .api.pipeline_api import router as pipeline_router
from .api.settings_api import router as settings_router
from .ws.logs import router as ws_logs_router
from .ws.progress import router as ws_progress_router

logger = logging.getLogger("dms-web")

STATIC_DIR = SERVICE_DIR / "static"


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="DMS Phân loại phản hồi",
        description="Giao diện quản lý hệ thống phân loại phản hồi DMS Rạng Đông",
        version="1.0.0",
    )

    # --- CORS ---
    # Configure via CORS_ALLOWED_ORIGINS env var (comma-separated).
    # Defaults to "*" for internal deployments. Set explicit origins in production
    # if the service is exposed to the internet.
    import os
    cors_origins_raw = os.environ.get("CORS_ALLOWED_ORIGINS", "*")
    cors_origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=cors_origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Shared state ---
    app.state.jobs = {}  # job_id -> job info dict

    # --- API routers ---
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
        return {"message": "DMS Phân loại phản hồi - API đang hoạt động"}

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


    # --- Startup ---
    @app.on_event("startup")
    async def on_startup():
        logger.info("DMS Web UI đang khởi động...")
        # Ensure work directories exist
        work_dir = SERVICE_DIR / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "input").mkdir(parents=True, exist_ok=True)
        (work_dir / "output").mkdir(parents=True, exist_ok=True)
        (work_dir / "checkpoint").mkdir(parents=True, exist_ok=True)
        log_dir = SERVICE_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.info("DMS Web UI sẵn sàng tại http://0.0.0.0:8000")

    return app
