"""WebSocket endpoint for live log streaming."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...settings import SERVICE_DIR

logger = logging.getLogger("dms-web")

router = APIRouter(tags=["ws"])

LOG_DIR = SERVICE_DIR / "logs"


def _find_latest_log() -> Path | None:
    """Find the most recent log file."""
    if not LOG_DIR.is_dir():
        return None
    log_files = sorted(LOG_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if log_files:
        return log_files[0]
    log_files = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return log_files[0] if log_files else None


def _parse_line(line: str) -> dict | None:
    """Parse a log line into a structured dict."""
    line = line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
        return {
            "timestamp": data.get("ts", ""),
            "level": data.get("level", "INFO"),
            "message": data.get("msg", line),
            "module": data.get("module", ""),
        }
    except json.JSONDecodeError:
        return {
            "timestamp": "",
            "level": "INFO",
            "message": line,
            "module": "",
        }


@router.websocket("/ws/logs")
async def ws_live_logs(websocket: WebSocket):
    """Stream log file changes in real-time.

    Query parameters:
    - level: filter by log level (DEBUG, INFO, WARNING, ERROR)

    Sends JSON messages:
    - {"type": "log", "data": {timestamp, level, message, module}}
    - {"type": "info", "data": {message}}
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

    # Parse level filter from query params
    level_filter = websocket.query_params.get("level", "").upper() or None

    log_file = _find_latest_log()
    if log_file is None:
        await websocket.send_json(
            {
                "type": "info",
                "data": {"message": "Không tìm thấy file log. Đang chờ..."},
            }
        )

    try:
        # Start at end of file (tail mode)
        last_position = 0
        if log_file is not None and log_file.is_file():
            last_position = log_file.stat().st_size

            # Send last 50 lines as initial context
            try:
                text = log_file.read_text(encoding="utf-8")
                lines = text.splitlines()
                initial_lines = lines[-50:] if len(lines) > 50 else lines
                for line in initial_lines:
                    parsed = _parse_line(line)
                    if parsed is None:
                        continue
                    if level_filter and parsed["level"] != level_filter:
                        continue
                    await websocket.send_json({"type": "log", "data": parsed})
            except Exception:
                pass

        current_log_file = log_file

        while True:
            # Check for new/rotated log file
            latest = _find_latest_log()
            if latest is not None and latest != current_log_file:
                current_log_file = latest
                last_position = 0
                await websocket.send_json(
                    {
                        "type": "info",
                        "data": {"message": f"Chuyển sang file log mới: {latest.name}"},
                    }
                )

            if current_log_file is not None and current_log_file.is_file():
                try:
                    current_size = current_log_file.stat().st_size
                    if current_size > last_position:
                        with open(current_log_file, encoding="utf-8") as f:
                            f.seek(last_position)
                            new_data = f.read()
                            last_position = f.tell()

                        for line in new_data.splitlines():
                            parsed = _parse_line(line)
                            if parsed is None:
                                continue
                            if level_filter and parsed["level"] != level_filter:
                                continue
                            await websocket.send_json({"type": "log", "data": parsed})

                    elif current_size < last_position:
                        # File was truncated/rotated
                        last_position = 0
                except Exception as exc:
                    logger.debug("Lỗi đọc log file: %s", exc)

            await asyncio.sleep(1.0)

    except WebSocketDisconnect:
        logger.debug("WebSocket log client ngắt kết nối")
    except Exception as exc:
        logger.warning("WebSocket log lỗi: %s", exc)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
