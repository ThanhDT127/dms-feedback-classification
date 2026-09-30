"""Server giả cho giao diện chat (b07 task 1.2) — chỉ dùng khi phát triển, không đóng gói.

Phục vụ SPA thật trong ``static/`` cùng REST đăng nhập/phiên tối thiểu và ``WS /ws/chat``
phát lại event từ ``tests/chat/fixtures/ui_events_*.json`` theo giao thức b06.

    uv run python scripts/chat_ws_fake_server.py --port 8765 --delay 0.25
    uv run python scripts/chat_ws_fake_server.py --drop-after 4      # ngắt kết nối sau seq 4 (một lần)
    uv run python scripts/chat_ws_fake_server.py --expire             # resume luôn nhận answer_expired

Đăng nhập bằng bất kỳ tài khoản/mật khẩu nào. Chọn kịch bản bằng từ khoá trong câu hỏi
(xem ``KEYWORDS``) hoặc gõ đúng tên fixture, ví dụ ``quote_xss``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

SERVICE_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = SERVICE_DIR / "static"
FIXTURE_DIR = SERVICE_DIR / "tests" / "chat" / "fixtures"
STORED_TYPES = {"data_block", "commentary", "refusal", "clarify", "suggestions", "error"}

# Từ khoá (không dấu hoá đơn giản bằng lower) → tên fixture.
KEYWORDS: list[tuple[str, str]] = [
    ("xss", "quote_xss"),
    ("thời tiết", "refusal"),
    ("vùng", "clarify"),
    ("lỗi", "error"),
    ("không nhận định", "commentary_unavailable"),
    ("lạ", "unknown_kind"),
    ("xu hướng", "timeseries"),
    ("đơn vị", "ranking_partial"),
    ("loại vấn đề", "donut"),
    ("so sánh", "table"),
    ("tổng quan", "overview"),
]


def load_fixtures() -> dict[str, dict[str, Any]]:
    return {
        path.stem.removeprefix("ui_events_"): json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(FIXTURE_DIR.glob("ui_events_*.json"))
    }


@dataclass
class Answer:
    answer_id: str
    owner: str
    session_id: str
    question: str
    events: list[dict[str, Any]] = field(default_factory=list)
    complete: bool = False
    cancelled: bool = False
    changed: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class FakeState:
    fixtures: dict[str, dict[str, Any]]
    delay: float
    drop_after: int
    expire: bool
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    messages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    answers: dict[str, Answer] = field(default_factory=dict)
    dropped_once: bool = False

    def pick(self, question: str) -> dict[str, Any]:
        text = question.strip().lower()
        if text in self.fixtures:
            return self.fixtures[text]
        for keyword, name in KEYWORDS:
            if keyword in text and name in self.fixtures:
                return self.fixtures[name]
        return self.fixtures["overview"]


def create_fake_app(state: FakeState) -> FastAPI:
    app = FastAPI(title="DMS chat fake server")

    def user_payload(username: str = "demo") -> dict[str, Any]:
        return {
            "username": username,
            "role": "admin",
            "display_name": "Người thử",
            "is_active": True,
        }

    @app.post("/api/auth/login")
    async def login(request: Request):
        body = await request.json()
        username = str(body.get("username") or "demo")
        return {
            "access_token": f"fake-{username}",
            "refresh_token": f"refresh-{username}",
            "token_type": "bearer",
            "user": user_payload(username),
        }

    @app.post("/api/auth/refresh")
    async def refresh():
        return {
            "access_token": "fake-demo",
            "refresh_token": "refresh-demo",
            "token_type": "bearer",
        }

    @app.post("/api/auth/logout")
    async def logout():
        return {"ok": True}

    @app.get("/api/auth/me")
    async def me():
        return user_payload()

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "uptime_seconds": 1, "version": "fake"}

    @app.get("/api/chat/config")
    async def chat_config():
        return {"enabled": True, "milestone": "M2", "max_question_chars": 1000}

    @app.get("/api/chat/sessions")
    async def list_sessions():
        ordered = sorted(state.sessions.values(), key=lambda s: s["updated_at"], reverse=True)
        return {
            "sessions": [
                {k: s[k] for k in ("session_id", "title", "created_at", "expires_at")}
                for s in ordered
            ]
        }

    def session_or_404(session_id: str) -> dict[str, Any]:
        if session_id not in state.sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        return state.sessions[session_id]

    @app.get("/api/chat/sessions/{session_id}/messages")
    async def messages(session_id: str):
        session_or_404(session_id)
        return {"session_id": session_id, "messages": state.messages.get(session_id, [])}

    @app.patch("/api/chat/sessions/{session_id}")
    async def rename(session_id: str, request: Request):
        session = session_or_404(session_id)
        title = str((await request.json()).get("title") or "").strip()
        if not 1 <= len(title) <= 120:
            raise HTTPException(status_code=422, detail="Invalid title")
        session["title"] = title
        return {k: session[k] for k in ("session_id", "title", "created_at", "expires_at")}

    @app.delete("/api/chat/sessions/{session_id}")
    async def delete(session_id: str):
        session_or_404(session_id)
        state.sessions.pop(session_id)
        state.messages.pop(session_id, None)
        return {"deleted": True, "session_id": session_id}

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def other_api(path: str):
        return JSONResponse({"detail": f"fake server: /api/{path} không có"}, status_code=404)

    @app.websocket("/ws/chat")
    async def ws_chat(websocket: WebSocket):
        await websocket.accept()
        lock = asyncio.Lock()
        pumps: dict[str, asyncio.Task[None]] = {}

        async def send(message: dict[str, Any]) -> None:
            async with lock:
                await websocket.send_json(message)

        async def pump(answer: Answer, after_seq: int) -> None:
            seq = after_seq
            while True:
                while seq < len(answer.events):
                    event = answer.events[seq]
                    await send({"answer_id": answer.answer_id, **event})
                    seq = event["seq"]
                    if state.drop_after and not state.dropped_once and seq >= state.drop_after:
                        state.dropped_once = True
                        await websocket.close(code=1012, reason="fake drop")
                        return
                if answer.complete:
                    return
                answer.changed.clear()
                await answer.changed.wait()

        def follow(answer: Answer, after_seq: int) -> None:
            if answer.answer_id in pumps:
                pumps[answer.answer_id].cancel()
            pumps[answer.answer_id] = asyncio.create_task(pump(answer, after_seq))

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    await send(
                        {
                            "type": "error",
                            "code": "BAD_MESSAGE",
                            "text": "Tin nhắn không đúng định dạng.",
                        }
                    )
                    continue
                kind = msg.get("type")
                if kind == "ping":
                    await send({"type": "pong"})
                elif kind == "ask":
                    question = str(msg.get("question") or "")
                    if any(not a.complete for a in state.answers.values()):
                        await send(
                            {
                                "type": "error",
                                "code": "BUSY",
                                "text": "Câu hỏi trước vẫn đang được xử lý.",
                                "client_msg_id": msg.get("client_msg_id"),
                            }
                        )
                        continue
                    session_id = msg.get("session_id")
                    now = datetime.now(UTC).isoformat()
                    if session_id and session_id not in state.sessions:
                        await send(
                            {
                                "type": "error",
                                "code": "SESSION_NOT_FOUND",
                                "text": "Không tìm thấy cuộc trò chuyện.",
                                "client_msg_id": msg.get("client_msg_id"),
                            }
                        )
                        continue
                    if not session_id:
                        session_id = uuid.uuid4().hex
                        state.sessions[session_id] = {
                            "session_id": session_id,
                            "title": question[:60],
                            "created_at": now,
                            "updated_at": now,
                            "expires_at": "",
                        }
                    answer = Answer(uuid.uuid4().hex, "demo", session_id, question)
                    state.answers[answer.answer_id] = answer
                    state.messages.setdefault(session_id, []).append(
                        {
                            "message_id": None,
                            "role": "user",
                            "content": question,
                            "created_at": now,
                            "metadata": {"answer_id": answer.answer_id},
                        }
                    )
                    await send(
                        {
                            "type": "ack",
                            "client_msg_id": msg.get("client_msg_id"),
                            "answer_id": answer.answer_id,
                            "session_id": session_id,
                        }
                    )
                    asyncio.create_task(produce(answer))
                    follow(answer, 0)
                elif kind == "resume":
                    answer = state.answers.get(str(msg.get("answer_id")))
                    if answer is None:
                        await send(
                            {
                                "type": "error",
                                "code": "ANSWER_NOT_FOUND",
                                "text": "Không tìm thấy câu trả lời.",
                            }
                        )
                    elif state.expire:
                        await send({"type": "answer_expired", "answer_id": answer.answer_id})
                    else:
                        follow(answer, int(msg.get("last_seq") or 0))
                elif kind == "cancel":
                    answer = state.answers.get(str(msg.get("answer_id")))
                    if answer is not None:
                        answer.cancelled = True
                else:
                    await send(
                        {
                            "type": "error",
                            "code": "BAD_MESSAGE",
                            "text": "Tin nhắn không đúng định dạng.",
                        }
                    )
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            for task in pumps.values():
                task.cancel()

    async def produce(answer: Answer) -> None:
        fixture = state.pick(answer.question)
        for event in fixture["events"]:
            await asyncio.sleep(state.delay)
            if answer.cancelled and event["type"] != "done":
                answer.events.append(
                    {
                        "seq": len(answer.events) + 1,
                        "type": "done",
                        "data": {
                            "status": "cancelled",
                            "summary": "",
                            "commentary_status": "skipped",
                            "dropped_sentences": 0,
                        },
                    }
                )
                break
            answer.events.append({**event, "seq": len(answer.events) + 1})
            answer.changed.set()
        answer.complete = True
        answer.changed.set()
        done = answer.events[-1]["data"]
        now = datetime.now(UTC).isoformat()
        state.messages.setdefault(answer.session_id, []).append(
            {
                "message_id": None,
                "role": "assistant",
                "content": done.get("summary", ""),
                "created_at": now,
                "metadata": {
                    "answer_id": answer.answer_id,
                    "done_status": done.get("status"),
                    "events": [e for e in answer.events if e["type"] in STORED_TYPES],
                },
            }
        )
        state.sessions[answer.session_id]["updated_at"] = now

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(str(STATIC_DIR / "index.html"), media_type="text/html")

    for sub in ("css", "js", "assets"):
        if (STATIC_DIR / sub).is_dir():
            app.mount(f"/{sub}", StaticFiles(directory=str(STATIC_DIR / sub)), name=sub)
    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--delay", type=float, default=0.25, help="Giây giữa hai event")
    parser.add_argument(
        "--drop-after", type=int, default=0, help="Đóng kết nối một lần sau seq này"
    )
    parser.add_argument("--expire", action="store_true", help="resume luôn nhận answer_expired")
    args = parser.parse_args(argv)
    state = FakeState(load_fixtures(), args.delay, args.drop_after, args.expire)
    print(f"Fixtures: {', '.join(state.fixtures)} → http://{args.host}:{args.port}/#chat")
    uvicorn.run(create_fake_app(state), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
