"""Message của kênh ``/ws/chat`` (design b06 D1).

Client gửi ``ask``/``resume``/``cancel``/``ping``; server trả ``ack``/event câu trả lời/
``answer_expired``/``pong``/``error``. Event câu trả lời dùng envelope b05 kèm ``answer_id``.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

MAX_ID_CHARS = 64


class ProtocolError(StrEnum):
    BAD_MESSAGE = "BAD_MESSAGE"
    BUSY = "BUSY"
    RATE_LIMITED = "RATE_LIMITED"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    ANSWER_NOT_FOUND = "ANSWER_NOT_FOUND"
    CHAT_DISABLED = "CHAT_DISABLED"
    INPUT_TOO_LONG = "INPUT_TOO_LONG"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INTERNAL = "INTERNAL"


ERROR_TEXTS: dict[ProtocolError, str] = {
    ProtocolError.BAD_MESSAGE: "Tin nhắn không đúng định dạng.",
    ProtocolError.BUSY: "Câu hỏi trước vẫn đang được xử lý, vui lòng đợi hoặc huỷ câu đó.",
    ProtocolError.RATE_LIMITED: "Bạn đã hỏi quá nhiều trong một phút, vui lòng thử lại sau.",
    ProtocolError.SESSION_NOT_FOUND: "Không tìm thấy cuộc trò chuyện.",
    ProtocolError.ANSWER_NOT_FOUND: "Không tìm thấy câu trả lời.",
    ProtocolError.CHAT_DISABLED: "Trợ lý hỏi đáp đang tắt.",
    ProtocolError.INPUT_TOO_LONG: "Câu hỏi quá dài.",
    ProtocolError.BUDGET_EXCEEDED: "Bạn đã dùng hết hạn mức hỏi đáp của hôm nay.",
    ProtocolError.INTERNAL: "Hệ thống đang gặp sự cố, vui lòng thử lại.",
}

_Id = Annotated[str, Field(min_length=1, max_length=MAX_ID_CHARS)]


class _ClientMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AskMessage(_ClientMessage):
    type: Literal["ask"]
    client_msg_id: _Id
    session_id: _Id | None = None
    # Độ dài tối đa kiểm ở handler theo CHAT_MAX_QUESTION_CHARS để trả INPUT_TOO_LONG riêng.
    question: str = Field(min_length=1)


class ResumeMessage(_ClientMessage):
    type: Literal["resume"]
    answer_id: _Id
    last_seq: int = Field(ge=0)


class CancelMessage(_ClientMessage):
    type: Literal["cancel"]
    answer_id: _Id


class PingMessage(_ClientMessage):
    type: Literal["ping"]


ClientMessage = Annotated[
    AskMessage | ResumeMessage | CancelMessage | PingMessage, Field(discriminator="type")
]
_CLIENT_ADAPTER: TypeAdapter[Any] = TypeAdapter(ClientMessage)


class BadMessage(ValueError):
    """Message không phải JSON hoặc sai schema; handler trả ``error(BAD_MESSAGE)``."""

    def __init__(self, detail: str, *, client_msg_id: str | None = None) -> None:
        super().__init__(detail)
        self.client_msg_id = client_msg_id


def parse_client_message(
    raw: str | bytes | dict[str, Any],
) -> AskMessage | ResumeMessage | CancelMessage | PingMessage:
    data: Any = raw
    if isinstance(raw, str | bytes):
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise BadMessage("not_json") from exc
    if not isinstance(data, dict):
        raise BadMessage("not_object")
    try:
        return _CLIENT_ADAPTER.validate_python(data)
    except ValidationError as exc:
        client_msg_id = data.get("client_msg_id")
        raise BadMessage(
            "schema",
            client_msg_id=client_msg_id if isinstance(client_msg_id, str) else None,
        ) from exc


def ack(*, client_msg_id: str, answer_id: str, session_id: str) -> dict[str, Any]:
    return {
        "type": "ack",
        "client_msg_id": client_msg_id,
        "answer_id": answer_id,
        "session_id": session_id,
    }


def answer_event(answer_id: str, event: dict[str, Any]) -> dict[str, Any]:
    return {"answer_id": answer_id, **event}


def answer_expired(answer_id: str) -> dict[str, Any]:
    return {"type": "answer_expired", "answer_id": answer_id}


def pong() -> dict[str, Any]:
    return {"type": "pong"}


def error(
    code: ProtocolError,
    *,
    client_msg_id: str | None = None,
    text: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "type": "error",
        "code": code.value,
        "text": text or ERROR_TEXTS[code],
    }
    if client_msg_id is not None:
        message["client_msg_id"] = client_msg_id
    if data:
        message["data"] = dict(data)
    return message
