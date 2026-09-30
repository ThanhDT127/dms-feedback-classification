"""Chạy thử một lượt chat từ dòng lệnh và in ``TurnOutcome`` dạng JSON (design b03 D12).

Ví dụ:
    python scripts/chat_plan_cli.py "Tổng quan tháng 8" --units TV1 --fake-llm
    python scripts/chat_plan_cli.py "Quý 2 so với quý 1 thế nào?" --admin --fake-llm
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

SERVICE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(SERVICE_SRC) not in sys.path:
    sys.path.insert(0, str(SERVICE_SRC))

from dms.chat.ai.orchestrator import ChatOrchestrator, OrchestratorConfig  # noqa: E402
from dms.chat.ai.planner_config import PlannerConfig  # noqa: E402
from dms.chat.ai.query_planner import QueryPlanner  # noqa: E402
from dms.chat.ai.schema_retriever import SchemaRetriever  # noqa: E402
from dms.chat.ai.text_match import normalize_match_text  # noqa: E402
from dms.chat.ai.types import HistoryTurn, LLMClient, LLMResult, TurnRequest  # noqa: E402
from dms.chat.contract import UserScope  # noqa: E402
from dms.chat.guardrails.plan_guard import PlanGuard, PlanGuardConfig  # noqa: E402
from dms.chat.mock_executor import MockQueryExecutor  # noqa: E402

FEWSHOT_PATH = SERVICE_SRC / "dms" / "chat" / "ai" / "data" / "planner_fewshot.jsonl"

# Giá trị mẫu để chạy thử không cần DB; bản thật lấy từ MetadataProvider khi ghép b06.
SAMPLE_VALUES: dict[str, list[str]] = {
    "units": [
        "Truyền thống Vùng 1",
        "Truyền thống Vùng 2",
        "Truyền thống Vùng 3",
        "Nha Trang",
        "Biên Hòa",
        "Hồ Chí Minh",
    ],
    "provinces": ["Hà Nội", "Hồ Chí Minh", "Khánh Hòa", "Đồng Nai"],
    "districts": ["Ba Đình", "Hoàn Kiếm", "Biên Hòa"],
    "products": ["Đèn LED Bulb", "Đèn LED Tube", "Bóng đèn huỳnh quang"],
    "statuses": ["Đã xử lý", "Chờ xử lý"],
}

CLARIFY_OUTPUT = json.dumps({"system_state": "CLARIFY", "confidence": 0.0, "steps": []})


class StaticMetadata:
    def valid_values(self) -> Mapping[str, Sequence[str]]:
        return SAMPLE_VALUES


class FewShotLLM:
    """LLM giả: tra ``planner_fewshot.jsonl`` theo câu hỏi đã chuẩn hoá; trượt thì CLARIFY."""

    def __init__(self, question: str, path: Path = FEWSHOT_PATH) -> None:
        self.question = question
        self.calls: list[str] = []
        self._plans: dict[str, str] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                key = normalize_match_text(str(record.get("question", "")))
                self._plans[key] = json.dumps(record.get("output", {}), ensure_ascii=False)

    def stream(
        self,
        prompt: str,
        *,
        call_type: str,
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ):
        """Nhận định giả cho ``--compose``: một câu không chứa số nên luôn qua sentence gate."""
        self.calls.append(call_type)
        return iter(["Chi tiết số liệu nằm ở các khối phía trên."])

    def generate_json(
        self, prompt: str, *, call_type: str, system_instruction: str | None = None
    ) -> LLMResult:
        self.calls.append(call_type)
        if call_type == "chat_contextualize":
            text = json.dumps(
                {"standalone_question": self.question, "is_follow_up": False}, ensure_ascii=False
            )
        else:
            text = self._plans.get(normalize_match_text(self.question), CLARIFY_OUTPUT)
        return LLMResult(text=text, usage={}, latency_ms=0, model="fake-fewshot")


def _real_llm() -> LLMClient:
    """Gemini thật, dùng khi không truyền ``--fake-llm``."""
    from dms.chat.ai.llm_gateway import GeminiJsonClient
    from dms.settings import get_settings

    return GeminiJsonClient(get_settings())


def _load_history(path: Path | None) -> list[HistoryTurn]:
    if path is None:
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        HistoryTurn(
            question=str(item.get("question", "")),
            answer_summary=str(item.get("answer_summary", "")),
        )
        for item in raw
    ]


def build_orchestrator(llm: LLMClient) -> ChatOrchestrator:
    metadata = StaticMetadata()
    planner_config = PlannerConfig()
    return ChatOrchestrator(
        llm=llm,
        planner=QueryPlanner(llm, SchemaRetriever(metadata, config=planner_config)),
        plan_guard=PlanGuard(metadata, config=PlanGuardConfig()),
        executor=MockQueryExecutor(),
        metadata=metadata,
        config=OrchestratorConfig(),
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chạy thử một lượt chat, in TurnOutcome JSON.")
    parser.add_argument("question", help="Câu hỏi của người dùng")
    scope_group = parser.add_mutually_exclusive_group()
    scope_group.add_argument("--units", default="", help="Đơn vị được phép, cách nhau bởi dấu phẩy")
    scope_group.add_argument("--admin", action="store_true", help="Chạy với quyền admin")
    parser.add_argument(
        "--history-file", type=Path, default=None, help="File JSON lịch sử hội thoại"
    )
    parser.add_argument(
        "--fake-llm", action="store_true", help="Dùng few-shot thay cho Gemini thật"
    )
    parser.add_argument(
        "--executor", choices=["mock"], default="mock", help="Executor dùng để chạy"
    )
    parser.add_argument(
        "--compose",
        action="store_true",
        help="In chuỗi event của câu trả lời (Response Shaper b05) thay cho TurnOutcome",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    units = [unit.strip() for unit in args.units.split(",") if unit.strip()]
    scope = UserScope(
        username="admin" if args.admin else "cli-user",
        role="admin" if args.admin else "user",
        display_name="CLI",
        unit_ids=[] if args.admin else units,
    )

    llm = FewShotLLM(args.question) if args.fake_llm else _real_llm()
    outcome = build_orchestrator(llm).handle(
        TurnRequest(
            question=args.question,
            scope=scope,
            session_id="cli",
            history=_load_history(args.history_file),
        )
    )
    if args.compose:
        from dms.chat.ai.answer_events import ListSink
        from dms.chat.ai.response_shaper import AnswerComposer

        sink = ListSink()
        AnswerComposer(llm=llm).compose(outcome, sink)
        print(json.dumps(sink.to_list(), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(outcome.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
