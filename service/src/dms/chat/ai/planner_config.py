"""Cấu hình Planner đọc từ settings (design b02 D10)."""

from __future__ import annotations

from dataclasses import dataclass

from ...settings import Settings


def parse_patterns(raw: str) -> frozenset[str]:
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


@dataclass(frozen=True)
class PlannerConfig:
    enabled_patterns: frozenset[str] = frozenset({"sql_template"})
    milestone: str = "M1"
    max_repair: int = 1
    metadata_ttl_seconds: int = 300

    @classmethod
    def from_settings(cls, settings: Settings) -> PlannerConfig:
        return cls(
            enabled_patterns=parse_patterns(settings.chat_enabled_patterns),
            milestone=settings.chat_milestone,
            max_repair=settings.chat_planner_max_repair,
            metadata_ttl_seconds=settings.chat_metadata_ttl_seconds,
        )
