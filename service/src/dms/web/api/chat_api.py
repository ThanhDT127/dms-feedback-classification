"""REST cho chatbot (design b06 D9, b10 D9).

``/api/chat/config`` luôn được đăng ký; ``/api/chat/sessions*`` và ``/api/chat/exports/*`` chỉ
khi ``CHAT_ENABLED``. Phiên và file xuất của người khác trả 404, kể cả với admin.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ...chat.ai.export.export_store import EXPORT_DIR_NAME, EXPORT_ID_PATTERN, ExportStore
from ...chat.contract import ChatSession
from ...chat.ws.session_service import RENAME_MAX_CHARS, SessionNotFound
from .. import deps
from ..deps import get_admin_user, get_current_user
from ..rate_limit import limiter

audit_logger = logging.getLogger("dms-chat-audit")
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

config_router = APIRouter(prefix="/api/chat", tags=["chat"])
router = APIRouter(prefix="/api/chat", tags=["chat"])

_CURRENT_USER = Annotated[dict, Depends(get_current_user)]
_ADMIN_USER = Annotated[dict, Depends(get_admin_user)]
USAGE_MAX_DAYS = 92


class RenameSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=RENAME_MAX_CHARS)


@config_router.get("/config")
async def chat_config(user: _CURRENT_USER) -> dict[str, Any]:
    settings = deps.get_settings()
    if settings is None:
        return {"enabled": False, "milestone": None, "max_question_chars": None}
    return {
        "enabled": bool(settings.chat_enabled),
        "milestone": settings.chat_milestone,
        "max_question_chars": int(settings.chat_max_question_chars),
    }


def _services():
    services = deps.get_chat_services()
    if services is None:
        raise HTTPException(status_code=503, detail="Chat unavailable")
    return services


def _session_dict(session: ChatSession) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "title": session.title,
        "created_at": session.created_at,
        "expires_at": session.expires_at,
    }


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Session not found")


@router.get("/sessions")
async def list_sessions(user: _CURRENT_USER) -> dict[str, Any]:
    services = _services()
    sessions = await run_in_threadpool(services.sessions.list_sessions, user["username"])
    return {"sessions": [_session_dict(s) for s in sessions]}


@router.get("/sessions/{session_id}/messages")
async def session_messages(session_id: str, user: _CURRENT_USER) -> dict[str, Any]:
    services = _services()
    try:
        messages = await run_in_threadpool(services.sessions.messages, user["username"], session_id)
    except SessionNotFound as exc:
        raise _not_found() from exc
    return {"session_id": session_id, "messages": messages}


@router.patch("/sessions/{session_id}")
@limiter.limit("30/minute")
async def rename_session(
    request: Request,
    response: Response,
    session_id: str,
    payload: RenameSessionRequest,
    user: _CURRENT_USER,
) -> dict[str, Any]:
    services = _services()
    if not payload.title.strip():
        raise HTTPException(status_code=422, detail="Invalid title")
    try:
        session = await run_in_threadpool(
            services.sessions.rename, user["username"], session_id, payload.title
        )
    except SessionNotFound as exc:
        raise _not_found() from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid title") from exc
    return _session_dict(session)


@router.delete("/sessions/{session_id}")
@limiter.limit("30/minute")
async def delete_session(
    request: Request, response: Response, session_id: str, user: _CURRENT_USER
) -> dict[str, Any]:
    services = _services()
    try:
        await run_in_threadpool(services.sessions.delete, user["username"], session_id)
    except SessionNotFound as exc:
        raise _not_found() from exc
    return {"deleted": True, "session_id": session_id}


# ── Tải file xuất (b10 D9) ──


def _export_store() -> ExportStore:
    settings = deps.get_settings()
    if settings is None:
        raise HTTPException(status_code=503, detail="Chat unavailable")
    return ExportStore(
        settings.work_dir / EXPORT_DIR_NAME,
        ttl_hours=int(settings.chat_export_ttl_hours),
        max_active_per_user=int(settings.chat_export_max_active_per_user),
    )


@router.get("/exports/{export_id}")
@limiter.limit("30/minute")
async def download_export(
    request: Request, response: Response, export_id: str, user: _CURRENT_USER
) -> FileResponse:
    """Chỉ chủ sở hữu tải được; mọi trường hợp khác là 404 để không lộ file có tồn tại hay không."""
    if not EXPORT_ID_PATTERN.match(export_id or ""):
        raise _export_not_found()
    store = _export_store()
    record = await run_in_threadpool(store.get, user["username"], export_id)
    if record is None:
        raise _export_not_found()
    path = store.file_path(user["username"], export_id)
    if not path.is_file():
        raise _export_not_found()
    audit_logger.info(
        "chat_export_downloaded",
        extra={
            "username": user["username"],
            "export_id": export_id,
            "size_bytes": record.size_bytes,
        },
    )
    filename = record.filename or f"{export_id}.xlsx"
    return FileResponse(
        path,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_ascii_filename(filename)}"; '
                f"filename*=utf-8\'\'{quote(filename)}"
            ),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


def _export_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Export not found")


def _ascii_filename(filename: str) -> str:
    cleaned = "".join(char for char in filename if 32 <= ord(char) < 127 and char != '"')
    return cleaned or "export.xlsx"


# ── Thống kê usage chat theo user (b11 D11) ──


@router.get("/usage")
async def chat_usage(
    user: _ADMIN_USER,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
) -> dict[str, Any]:
    """Token và chi phí theo user theo ngày; không có nội dung câu hỏi hay câu trả lời."""
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="date_to must be >= date_from")
    if (date_to - date_from).days + 1 > USAGE_MAX_DAYS:
        raise HTTPException(status_code=422, detail=f"Range must be at most {USAGE_MAX_DAYS} days")
    services = _services()
    ledger = getattr(services, "ledger", None)
    if ledger is None:
        return {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "rows": []}
    rows = await run_in_threadpool(ledger.daily_summary, date_from, date_to)
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "rows": [row.to_dict() for row in rows],
    }
