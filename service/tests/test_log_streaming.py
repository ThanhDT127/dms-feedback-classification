from __future__ import annotations

from pathlib import Path

from dms.web.ws.logs import _tail_lines


def test_tail_lines_reads_bounded_suffix(tmp_path):
    log_path = tmp_path / "large.log"
    log_path.write_text("\n".join(f"line-{idx:04d}" for idx in range(1000)), encoding="utf-8")

    lines = _tail_lines(log_path, max_lines=5, max_bytes=128)

    assert lines == [
        "line-0995",
        "line-0996",
        "line-0997",
        "line-0998",
        "line-0999",
    ]


def test_web_startup_log_uses_service_port():
    app_source = (Path(__file__).resolve().parents[1] / "src" / "dms" / "web" / "app.py").read_text(
        encoding="utf-8"
    )

    assert "http://0.0.0.0:8501" in app_source
    assert "http://0.0.0.0:8000" not in app_source
