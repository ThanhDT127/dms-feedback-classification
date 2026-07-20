"""WebSocket endpoint for live log streaming."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...settings import SERVICE_DIR
from .connection_limiter import WS_LIMIT_CLOSE_CODE, ws_connection_limiter

logger = logging.getLogger("dms-web")

router = APIRouter(tags=["ws"])

LOG_DIR = SERVICE_DIR / "logs"
DEFAULT_INITIAL_TAIL_LINES = 50
DEFAULT_INITIAL_TAIL_BYTES = 64 * 1024


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


def _tail_lines(path: Path, max_lines: int, max_bytes: int) -> list[str]:
    """Read a bounded tail from a log file."""
    if max_lines <= 0 or max_bytes <= 0:
        return []
    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - max_bytes))
        data = handle.read(max_bytes)
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return lines[-max_lines:]


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
        await websocket.close(code=4001, reason="Authentication required")
        return
    from .. import deps

    settings = deps.get_settings()
    if not settings:
        await websocket.close(code=4001, reason="Server configuration unavailable")
        return

    import jwt as pyjwt

    from ...jwt_utils import decode_token
    from ...token_blacklist import is_revoked

    try:
        payload = decode_token(token, settings.jwt_secret_key, expected_type="access")
    except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError, ValueError):
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    if payload.get("jti") and is_revoked(payload["jti"]):
        await websocket.close(code=4001, reason="Token has been revoked")
        return

    identity = ws_connection_limiter.identity_for(websocket, payload.get("sub"))
    if not ws_connection_limiter.acquire("logs", identity):
        await websocket.close(
            code=WS_LIMIT_CLOSE_CODE, reason="WebSocket connection limit exceeded"
        )
        return

    await websocket.accept()

    # Parse level filter from query params
    level_filter = websocket.query_params.get("level", "").upper() or None
    initial_tail_lines = int(
        getattr(settings, "log_ws_initial_tail_lines", DEFAULT_INITIAL_TAIL_LINES)
    )
    initial_tail_bytes = int(
        getattr(settings, "log_ws_initial_tail_bytes", DEFAULT_INITIAL_TAIL_BYTES)
    )

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
                for line in _tail_lines(log_file, initial_tail_lines, initial_tail_bytes):
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
        ws_connection_limiter.release("logs", identity)
