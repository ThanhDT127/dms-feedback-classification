"""WebSocket endpoint for classification job progress updates."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .connection_limiter import WS_LIMIT_CLOSE_CODE, ws_connection_limiter

logger = logging.getLogger("dms-web")

router = APIRouter(tags=["ws"])


@router.websocket("/ws/classify/{job_id}")
async def ws_classify_progress(websocket: WebSocket, job_id: str):
    """Stream classification progress for a specific durable job."""
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Authentication required")
        return

    from .. import deps

    settings = deps.get_settings()
    if not settings:
        await websocket.close(code=4001, reason="Server configuration unavailable")
        return

    import jwt as pyjwt

    from ...jwt_utils import decode_token

    try:
        payload = decode_token(token, settings.jwt_secret_key, expected_type="access")
    except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError, ValueError):
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    from ...token_blacklist import is_revoked

    if payload.get("jti") and is_revoked(payload["jti"]):
        await websocket.close(code=4001, reason="Token has been revoked")
        return

    username = payload.get("sub")
    if not username:
        await websocket.close(code=4001, reason="Invalid token payload")
        return

    user_store = deps.get_user_store()
    job_store = deps.get_classification_job_store()
    if user_store is None or job_store is None:
        await websocket.close(code=4001, reason="Server state unavailable")
        return

    user = user_store.get_user(username)
    if not user or user.get("is_active", True) is False:
        await websocket.close(code=4001, reason="User unavailable")
        return

    identity = ws_connection_limiter.identity_for(websocket, user.get("username") or username)
    if not ws_connection_limiter.acquire("classify", identity):
        await websocket.close(code=WS_LIMIT_CLOSE_CODE, reason="WebSocket connection limit exceeded")
        return

    job = job_store.get_job(job_id, include_results=False)
    if job is None or not (
        user.get("role") == "admin" or job.get("owner_username") == user.get("username")
    ):
        await websocket.accept()
        await websocket.send_json(
            {
                "type": "error",
                "data": {"error": f"Khong tim thay job: {job_id}"},
            }
        )
        await websocket.close(code=4004)
        ws_connection_limiter.release("classify", identity)
        return

    await websocket.accept()

    last_sent_rows = -1
    last_sent_result_seq = 0
    last_sent_step = None
    last_sent_step_status = None
    terminal_sent = False

    try:
        while True:
            job = job_store.get_job(job_id, include_results=False)
            if job is None:
                await websocket.send_json(
                    {
                        "type": "error",
                        "data": {"error": "Job da bi xoa"},
                    }
                )
                break

            status = job.get("status", "unknown")

            new_results = job_store.list_results_after(job_id, last_sent_result_seq)
            if new_results:
                last_sent_result_seq = max(int(r.get("_seq", 0)) for r in new_results)
                await websocket.send_json(
                    {"type": "batch_result", "data": {"results": new_results}}
                )

            current_rows = job.get("rows_done", 0)
            current_step = job.get("step")
            current_step_status = job.get("step_status")

            rows_changed = current_rows != last_sent_rows
            step_changed = (
                current_step != last_sent_step or current_step_status != last_sent_step_status
            )
            terminal = status in ("completed", "error", "cancelled")

            if rows_changed or step_changed or terminal:
                last_sent_rows = current_rows
                last_sent_step = current_step
                last_sent_step_status = current_step_status

                if status == "completed":
                    await websocket.send_json(
                        {
                            "type": "complete",
                            "data": {
                                "job_id": job_id,
                                "status": "completed",
                                "total_rows": job.get("total_rows", 0),
                                "rows_done": job.get("rows_done", 0),
                                "percent": 100,
                                "output_path": job.get("output_path", ""),
                                "duration_seconds": job.get("duration_seconds", 0),
                                "sp_web_url": job.get("sp_web_url"),
                            },
                        }
                    )
                    terminal_sent = True
                    break

                if status == "error":
                    await websocket.send_json(
                        {
                            "type": "error",
                            "data": {
                                "job_id": job_id,
                                "status": "error",
                                "error": job.get("error", "Loi khong xac dinh"),
                            },
                        }
                    )
                    terminal_sent = True
                    break

                if status == "cancelled":
                    await websocket.send_json(
                        {
                            "type": "cancelled",
                            "data": {
                                "job_id": job_id,
                                "status": "cancelled",
                            },
                        }
                    )
                    terminal_sent = True
                    break

                await websocket.send_json(
                    {
                        "type": "progress",
                        "data": {
                            "job_id": job_id,
                            "status": status,
                            "total_rows": job.get("total_rows", 0),
                            "rows_done": current_rows,
                            "percent": job.get("percent", 0),
                            "step": current_step,
                            "step_status": current_step_status,
                        },
                    }
                )

            await asyncio.sleep(0.5)

    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected for job %s", job_id)
    except Exception as exc:
        logger.warning("WebSocket error for job %s: %s", job_id, exc)
        if not terminal_sent:
            try:
                await websocket.send_json(
                    {
                        "type": "error",
                        "data": {"job_id": job_id, "status": "error", "error": str(exc)},
                    }
                )
            except Exception:
                pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
        ws_connection_limiter.release("classify", identity)
