"""Chạy đánh giá nhận định chế độ 3 với Gemini thật (cần credential).

Ví dụ:
    uv run python scripts/eval_chat_synthesis.py --limit 10
"""

from __future__ import annotations

from dms.chat.ai.synthesis_eval import main

if __name__ == "__main__":
    raise SystemExit(main())
