"""Bản ghi audit ``chat_turn_audit`` cho mỗi lượt (design b06 D11).

Chỉ gồm định danh, quyết định, thời gian và chi phí — **không** có câu hỏi, câu trả lời hay dữ liệu.
"""

from __future__ import annotations

import logging
from typing import Any

from .turn_runner import TurnRecord

audit_logger = logging.getLogger("dms-chat-audit")
AUDIT_EVENT = "chat_turn_audit"


def audit_fields(record: TurnRecord) -> dict[str, Any]:
    outcome = record.outcome
    compose = record.compose
    return {
        "request_id": outcome.request_id if outcome else record.turn.request_id,
        "answer_id": record.answer_id,
        "username": record.username,
        "session_id": record.session_id,
        "intent": outcome.intent.value if outcome and outcome.intent else None,
        "decision": outcome.decision.value if outcome else None,
        "done_status": record.done_status.value,
        "functions": [step.function_name for step in outcome.step_results] if outcome else [],
        "scope_units": list(outcome.scope_units) if outcome else [],
        "dropped_units": [
            entity.value for entity in outcome.dropped_entities if entity.dimension == "unit"
        ]
        if outcome
        else [],
        "timings_ms": dict(outcome.timings_ms) if outcome else {},
        "queue_ms": record.queue_ms,
        "total_ms": record.total_ms,
        "llm_usage": {k: dict(v) for k, v in outcome.llm_usage.items()} if outcome else {},
        "dropped_sentences": compose.dropped_sentences if compose else 0,
        "error_type": record.error.split(":", 1)[0] if record.error else None,
        # SQL Pattern 2 đã qua guard (≤ 2.000 ký tự) để điều tra khi có khiếu nại (b09 D8).
        "sql": [
            {"step": step.index, "sql": step.sql, "repairs": step.sql_repairs}
            for step in (outcome.step_results if outcome else ())
            if getattr(step, "sql", None)
        ],
    }


def log_turn_audit(record: TurnRecord) -> None:
    audit_logger.info(AUDIT_EVENT, extra={"audit": audit_fields(record)})
