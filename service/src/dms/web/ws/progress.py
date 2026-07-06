"""WebSocket endpoint for classification job progress updates."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("dms-web")

router = APIRouter(tags=["ws"])


@router.websocket("/ws/classify/{job_id}")
async def ws_classify_progress(websocket: WebSocket, job_id: str):
    """Stream classification progress for a specific job.

    Sends periodic JSON messages with job status:
    - {"type": "progress", "data": {status, percent, rows_done, total_rows}}
    - {"type": "complete", "data": {output_path, duration_seconds, ...}}
    - {"type": "error", "data": {error}}
    """
    # Validate authentication token
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Missing authentication token")
        return
    from .. import deps
    settings = deps.get_settings()
    if settings:
        from ...jwt_utils import decode_token
        import jwt as pyjwt
        try:
            decode_token(token, settings.jwt_secret_key, expected_type="access")
        except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError, ValueError):
            await websocket.close(code=4001, reason="Invalid or expired token")
            return

    await websocket.accept()

    jobs: dict = websocket.app.state.jobs
    job = jobs.get(job_id)
    if job is None:
        await websocket.send_json(
            {
                "type": "error",
                "data": {"error": f"Không tìm thấy job: {job_id}"},
            }
        )
        await websocket.close(code=4004)
        return

    last_sent_rows = -1
    last_sent_results_count = 0
    last_sent_step = None
    last_sent_step_status = None
    try:
        while True:
            job = jobs.get(job_id)
            if job is None:
                await websocket.send_json(
                    {
                        "type": "error",
                        "data": {"error": "Job đã bị xóa"},
                    }
                )
                break

            status = job.get("status", "unknown")

            # 1. Send any new batch results
            all_results = job.get("results", [])
            if len(all_results) > last_sent_results_count:
                new_results = all_results[last_sent_results_count:]
                last_sent_results_count = len(all_results)
                await websocket.send_json(
                    {"type": "batch_result", "data": {"results": new_results}}
                )

            # 2. Send progress update when rows_done OR step changes
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
                    # Send final batch results if any are left
                    if len(all_results) > last_sent_results_count:
                        new_results = all_results[last_sent_results_count:]
                        await websocket.send_json(
                            {"type": "batch_result", "data": {"results": new_results}}
                        )

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
                            },
                        }
                    )
                    break

                if status == "error":
                    await websocket.send_json(
                        {
                            "type": "error",
                            "data": {
                                "job_id": job_id,
                                "status": "error",
                                "error": job.get("error", "Lỗi không xác định"),
                            },
                        }
                    )
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
                    break

                # Progress update
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
        logger.debug("WebSocket client ngắt kết nối cho job %s", job_id)
    except Exception as exc:
        logger.warning("WebSocket lỗi cho job %s: %s", job_id, exc)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
