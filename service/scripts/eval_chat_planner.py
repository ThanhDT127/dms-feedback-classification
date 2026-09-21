"""Chạy đánh giá Query Planner với Gemini thật.

Ví dụ:
    uv run python scripts/eval_chat_planner.py --repeat 2
"""

from __future__ import annotations

from dms.chat.ai.planner_eval import main

if __name__ == "__main__":
    raise SystemExit(main())
