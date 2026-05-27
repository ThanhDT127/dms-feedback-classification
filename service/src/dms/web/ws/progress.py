"""WebSocket endpoint for classification job progress updates."""

from __future__ import annotations

import asyncio
import json
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
    await websocket.accept()

    jobs: dict = websocket.app.state.jobs
    job = jobs.get(job_id)
    if job is None:
        await websocket.send_json({
            "type": "error",
            "data": {"error": f"Không tìm thấy job: {job_id}"},
        })
        await websocket.close(code=4004)
        return

    last_sent_rows = -1
    try:
        while True:
            job = jobs.get(job_id)
            if job is None:
                await websocket.send_json({
                    "type": "error",
                    "data": {"error": "Job đã bị xóa"},
                })
                break

            status = job.get("status", "unknown")

            # Send progress update when rows_done changes
            current_rows = job.get("rows_done", 0)
            if current_rows != last_sent_rows or status in ("completed", "error", "cancelled"):
                last_sent_rows = current_rows

                if status == "completed":
                    await websocket.send_json({
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
                    })
                    break

                if status == "error":
                    await websocket.send_json({
                        "type": "error",
                        "data": {
                            "job_id": job_id,
                            "status": "error",
                            "error": job.get("error", "Lỗi không xác định"),
                        },
                    })
                    break

                if status == "cancelled":
                    await websocket.send_json({
                        "type": "cancelled",
                        "data": {
                            "job_id": job_id,
                            "status": "cancelled",
                        },
                    })
                    break

                # Progress update
                await websocket.send_json({
                    "type": "progress",
                    "data": {
                        "job_id": job_id,
                        "status": status,
                        "total_rows": job.get("total_rows", 0),
                        "rows_done": current_rows,
                        "percent": job.get("percent", 0),
                    },
                })

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
