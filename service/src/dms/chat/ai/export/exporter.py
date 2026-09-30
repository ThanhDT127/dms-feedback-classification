"""Ghép khối của một lượt thành file Excel đã lưu (spec ``chat-report-export``, b10 D7–D9).

``AnswerComposer`` gọi ``export()`` khi lượt là yêu cầu xuất file; mọi lỗi được trả về dưới
dạng khối ``export`` có ``error`` chứ không làm hỏng câu trả lời đã stream.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..date_resolver import DEFAULT_TIMEZONE, resolve_timezone
from ..intents import Intent
from ..types import Reason, TurnOutcome
from .export_store import EXPORT_DIR_NAME, ExportStore
from .xlsx_writer import ExportInfo, ExportTooLarge, write_workbook

logger = logging.getLogger("dms-chat-export")

REPORT_FILENAME_PREFIX: dict[Intent, str] = {
    Intent.REPORT_DAILY: "bao-cao-ngay",
    Intent.REPORT_WEEKLY: "bao-cao-tuan",
    Intent.REPORT_CUSTOM: "bao-cao",
}
ANSWER_FILENAME_PREFIX = "ket-qua-chat"
ALL_UNITS_LABEL = "Toàn công ty"


@dataclass(frozen=True)
class ExportConfig:
    max_rows: int = 5000
    max_bytes: int = 10 * 1024 * 1024
    ttl_hours: int = 24
    max_active_per_user: int = 20
    timezone: str = DEFAULT_TIMEZONE

    @classmethod
    def from_settings(cls, settings: Any) -> ExportConfig:
        return cls(
            max_rows=int(settings.chat_export_max_rows),
            max_bytes=int(settings.chat_export_max_bytes),
            ttl_hours=int(settings.chat_export_ttl_hours),
            max_active_per_user=int(settings.chat_export_max_active_per_user),
            timezone=str(settings.chat_timezone),
        )


class ChatExporter:
    def __init__(
        self,
        store: ExportStore,
        *,
        config: ExportConfig | None = None,
        now: Any = None,
    ) -> None:
        self.store = store
        self.config = config or ExportConfig()
        self._now = now or (lambda: datetime.now(UTC))

    @classmethod
    def from_settings(cls, settings: Any, **kwargs: Any) -> ChatExporter:
        config = ExportConfig.from_settings(settings)
        store = ExportStore(
            Path(settings.work_dir) / EXPORT_DIR_NAME,
            ttl_hours=config.ttl_hours,
            max_active_per_user=config.max_active_per_user,
        )
        return cls(store, config=config, **kwargs)

    # ── API dùng bởi AnswerComposer ──

    def export(
        self,
        *,
        outcome: TurnOutcome,
        blocks: Sequence[Any],
        highlights: Sequence[str] = (),
        commentary: Sequence[str] = (),
        range_text: str = "",
    ) -> dict[str, Any]:
        owner = _owner(outcome)
        if not owner:
            logger.warning("chat_export_without_owner", extra={"request_id": outcome.request_id})
            return {"error": Reason.EXPORT_FAILED.value}

        local_now = self._now().astimezone(resolve_timezone(self.config.timezone))
        filename = _filename(outcome, local_now)
        info = ExportInfo(
            title=_title(outcome),
            exported_by=_display_name(outcome) or owner,
            exported_at=local_now.strftime("%d/%m/%Y %H:%M"),
            scope=", ".join(outcome.scope_units) or ALL_UNITS_LABEL,
            date_range=range_text,
            filters=_filters(outcome),
            assumptions=tuple(outcome.assumptions),
            notes=tuple(notice.message for notice in outcome.notices),
            highlights=tuple(highlights),
            commentary=tuple(commentary),
        )
        payloads = [
            block.to_dict() if hasattr(block, "to_dict") else dict(block) for block in blocks
        ]

        export_id, path = self.store.reserve(owner)
        try:
            written = write_workbook(
                path,
                info,
                payloads,
                max_rows=self.config.max_rows,
                max_bytes=self.config.max_bytes,
            )
        except ExportTooLarge:
            logger.warning("chat_export_too_large", extra={"request_id": outcome.request_id})
            return {"error": Reason.EXPORT_TOO_LARGE.value}
        except Exception:
            logger.exception("chat_export_write_failed", extra={"request_id": outcome.request_id})
            self.store.delete(owner, export_id)
            return {"error": Reason.EXPORT_FAILED.value}

        record = self.store.save_metadata(
            owner,
            export_id,
            filename=filename,
            size_bytes=written.size_bytes,
            rows_exported=written.rows_exported,
            truncated=written.truncated,
            now=self._now(),
        )
        logger.info(
            "chat_export_created",
            extra={
                "request_id": outcome.request_id,
                "export_id": export_id,
                "size_bytes": record.size_bytes,
                "rows": record.rows_exported,
                "truncated": record.truncated,
            },
        )
        return {
            "export_id": record.export_id,
            "filename": record.filename,
            "size_bytes": record.size_bytes,
            "expires_at": record.expires_at,
            "rows_exported": record.rows_exported,
            "truncated": record.truncated,
        }


# ── Tiện ích ──


def _owner(outcome: TurnOutcome) -> str:
    return str(outcome.username or "")


def _display_name(outcome: TurnOutcome) -> str:
    return str(outcome.display_name or "")


def _title(outcome: TurnOutcome) -> str:
    if outcome.report_type is not None:
        return {
            Intent.REPORT_DAILY: "Báo cáo ngày",
            Intent.REPORT_WEEKLY: "Báo cáo tuần",
            Intent.REPORT_CUSTOM: "Báo cáo",
        }.get(outcome.report_type, "Báo cáo")
    return outcome.original_query or "Kết quả chat"


def _filters(outcome: TurnOutcome) -> tuple[str, ...]:
    from ..function_catalog import PARAM_LABELS_VI

    plan = outcome.validated_plan
    if plan is None or not plan.steps:
        return ()
    params: Mapping[str, Any] = plan.steps[0].params
    skip = {"date_from", "date_to", "compare_from", "compare_to", "limit", "page", "page_size"}
    return tuple(
        f"{PARAM_LABELS_VI.get(key, key)}: {value}"
        for key, value in params.items()
        if key not in skip and isinstance(value, str | int | float) and value != ""
    )


def _filename(outcome: TurnOutcome, moment: datetime) -> str:
    if outcome.report_type is not None and outcome.report_range is not None:
        prefix = REPORT_FILENAME_PREFIX.get(outcome.report_type, "bao-cao")
        start = outcome.report_range.date_from.isoformat()
        end = outcome.report_range.date_to.isoformat()
        return f"{prefix}_{start}_{end}.xlsx"
    return f"{ANSWER_FILENAME_PREFIX}_{moment.strftime('%Y%m%d-%H%M')}.xlsx"


__all__ = ["ALL_UNITS_LABEL", "ChatExporter", "ExportConfig"]
