from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "chat_plan_cli.py"


def run_cli(*args: str) -> dict:
    process = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


@pytest.mark.parametrize(
    ("args", "expected_decision"),
    [
        (("Tổng quan tháng 8", "--units", "TV1", "--fake-llm"), "clarify"),
        (
            ("Tổng quan phản hồi của Hồ Chí Minh quý 2", "--admin", "--fake-llm"),
            "run",
        ),
    ],
    ids=["fewshot_miss_clarifies", "fewshot_hit_runs"],
)
def test_cli_prints_valid_turn_outcome(args, expected_decision):
    outcome = run_cli(*args)
    assert outcome["decision"] == expected_decision
    assert outcome["request_id"]
    assert "timings_ms" in outcome and "total" in outcome["timings_ms"]


def test_cli_uses_mock_executor_by_default():
    outcome = run_cli("Tổng quan phản hồi của Hồ Chí Minh quý 2", "--admin", "--fake-llm")
    assert outcome["step_results"], "phải có ít nhất một bước chạy qua executor"
    assert outcome["step_results"][0]["status"]["state"] in {"ok", "no_data"}


def test_cli_compose_prints_event_stream():
    events = run_cli(
        "Tổng quan phản hồi của Hồ Chí Minh quý 2", "--admin", "--fake-llm", "--compose"
    )

    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert events[-1]["type"] == "done"
    assert any(event["type"] == "data_block" for event in events)
