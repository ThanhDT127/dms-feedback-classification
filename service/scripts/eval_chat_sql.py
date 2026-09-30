"""Đánh giá Pattern 2 (text2sql) với Gemini thật trên DB fixture (b09 task 7.2).

    uv run python scripts/eval_chat_sql.py --pause 1.5 [--limit 10] [--out work/eval/sql.json]

Dựng DB fixture tạm từ ``tests/chat/sql_fixture.py`` (cần thư mục tests), chạy SQL qua view đã áp
phạm vi (admin) và in execution accuracy, số lần sửa trung bình, phân bố mã lỗi guard, token.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_DIR / "tests"))
sys.path.insert(0, str(SERVICE_DIR / "tests" / "chat"))


def main(argv: list[str] | None = None, *, llm=None) -> int:
    from sql_fixture import BH, NT, TV1, TV2, build_sql_fixture, run_scoped_sql

    from dms.analytics import AnalyticsFilter
    from dms.chat.ai.sql_eval import DEFAULT_CASES_PATH, evaluate, load_cases
    from dms.chat.ai.sql_generator import SqlGeneratorConfig
    from dms.chat.contract import UserScope

    parser = argparse.ArgumentParser(description="Đánh giá text2sql")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--pause", type=float, default=0.0)
    parser.add_argument("--max-repair", type=int, default=2)
    args = parser.parse_args(argv)

    db = Path(tempfile.mkdtemp()) / "sql_eval.db"
    service = build_sql_fixture(db)
    admin = UserScope(username="eval", role="admin", display_name="Eval", unit_ids=[])

    def run_sql(sql: str):
        result = run_scoped_sql(db, sql, admin)
        return result.status.value, list(result.data or []), result.error_message or ""

    everything = AnalyticsFilter()
    metadata_values = {
        "units": [TV1, TV2, NT, BH],
        "products": service.issue_filter_options(everything).get("products", []),
        "statuses": ["Chờ xử lý", "Đã xử lý"],
        "sources": ["Zalo", "Hotline", "Email"],
    }
    if llm is None:
        from dms.chat.ai.llm_gateway import GeminiChatGateway
        from dms.settings import get_settings

        llm = GeminiChatGateway(get_settings())
    cases = load_cases(args.cases)
    if args.limit:
        cases = cases[: args.limit]
    report = evaluate(
        cases,
        llm=llm,
        run_sql=run_sql,
        metadata_values=metadata_values,
        max_repair=args.max_repair,
        config=SqlGeneratorConfig(),
        pause_seconds=args.pause,
    )
    out = args.out or SERVICE_DIR / "work" / "eval" / f"sql-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"{report['cases']} ca · accuracy {report['execution_accuracy']} · sửa TB {report['avg_repairs']} · "
        f"mã guard {report['guard_codes']} · token {report['tokens']} · valid_run {report['valid_run']} → {out}"
    )
    return 0 if report["accuracy_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
