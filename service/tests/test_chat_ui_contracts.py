"""Contract giao diện chat (b07 D10) — đọc mã nguồn JS, không chạy trình duyệt.

Kiểm hành vi thật của ``chat-store.js`` bằng ``node`` khi máy có node (bỏ qua nếu không).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from test_frontend_contracts import _page_exports, _page_refs

STATIC = Path(__file__).resolve().parents[1] / "static"
FIXTURES = Path(__file__).resolve().parent / "chat" / "fixtures"
ALLOWED_INNER_HTML_CONSTANTS: set[str] = set()
NODE = shutil.which("node")


def _read(rel: str) -> str:
    return (STATIC / rel).read_text(encoding="utf-8")


def test_chat_page_handler_references_are_exported():
    chat_js = _read("js/pages/chat.js")
    exports = _page_exports(chat_js)
    assert {"render", "destroy"} <= exports
    referenced = set()
    for rel in (
        "js/pages/chat.js",
        "js/app.js",
        "js/components/sidebar.js",
        "js/components/chat-blocks.js",
    ):
        referenced |= _page_refs(_read(rel), "ChatPage")
    assert referenced <= exports, referenced - exports


def test_chat_blocks_never_uses_inner_html():
    blocks = _read("js/components/chat-blocks.js")
    assert "innerHTML" not in blocks
    assert "insertAdjacentHTML" not in blocks
    assert "outerHTML" not in blocks
    assert "textContent" in blocks and "createElement" in blocks


def _inner_html_templates(js: str) -> list[str]:
    return re.findall(r"innerHTML\s*=\s*`(.*?)`", js, flags=re.S)


def test_chat_page_inner_html_interpolations_are_escaped():
    chat_js = _read("js/pages/chat.js")
    assert "insertAdjacentHTML" not in chat_js and "outerHTML" not in chat_js
    # innerHTML chỉ được gán bằng template literal để kiểm được phần nội suy.
    assert len(re.findall(r"innerHTML\s*=", chat_js)) == len(_inner_html_templates(chat_js))
    for template in _inner_html_templates(chat_js):
        for expr in re.findall(r"\$\{(.*?)\}", template, flags=re.S):
            expr = expr.strip()
            assert expr.startswith("ChatBlocks.escape(") or expr in ALLOWED_INNER_HTML_CONSTANTS, (
                expr
            )


def test_chat_store_is_pure_and_handles_duplicates_and_gaps():
    store = _read("js/components/chat-store.js")
    assert "document" not in store and "WebSocket" not in store and "fetch(" not in store
    assert "seq <= answer.lastSeq" in store
    assert "seq > answer.lastSeq + 1" in store
    assert "needsResume: true" in store


def test_chat_page_resumes_on_open_and_on_gap_and_handles_expired():
    chat_js = _read("js/pages/chat.js")
    on_open = chat_js[chat_js.index("function onOpen()") : chat_js.index("function onClose()")]
    assert "ChatState.resumeMessages()" in on_open and "sendResume" in on_open
    assert "type: 'resume'" in chat_js
    assert "ChatState.APPLY.GAP" in chat_js
    assert "case 'answer_expired':" in chat_js
    expired = chat_js[chat_js.index("function handleExpired(") :]
    assert "reloadCurrentSession()" in expired[: expired.index("\n  }\n")]
    assert "`/chat/sessions/${encodeURIComponent(sessionId)}/messages`" in chat_js


def test_chat_page_uses_existing_ws_client_without_changing_ws_js():
    chat_js = _read("js/pages/chat.js")
    assert "extends WS.WSClient" in chat_js
    assert "'/ws/chat'" in chat_js
    assert "new WebSocket(" not in chat_js
    ws_js = _read("js/ws.js")
    assert "chat" not in ws_js.lower()


def test_chat_page_destroy_closes_socket_and_charts():
    chat_js = _read("js/pages/chat.js")
    destroy = chat_js[
        chat_js.index("function destroy()") : chat_js.index("function toggleSessions(")
    ]
    assert "client.close()" in destroy
    assert "ChatBlocks.destroyCharts()" in destroy


def test_chat_scripts_load_in_dependency_order():
    index = _read("index.html")
    order = [
        "js/api.js",
        "js/ws.js",
        "js/components/charts.js",
        "js/components/chat-store.js",
        "js/components/chat-blocks.js",
        "js/pages/chat.js",
        "js/app.js",
    ]
    positions = [index.index(f'src="{src}?v=') for src in order]
    assert positions == sorted(positions)
    assert 'href="css/chat.css?v=' in index


def test_chat_route_and_sidebar_follow_feature_flag():
    app_js = _read("js/app.js")
    sidebar_js = _read("js/components/sidebar.js")
    assert re.search(r"chat:\s*\{\s*module:\s*\(\)\s*=>\s*window\.ChatPage", app_js)
    assert "pageName === 'chat' && !state.chatConfig?.enabled" in app_js
    assert "API.get('/chat/config', { silent: true })" in app_js
    assert "item.id === 'chat' && !App.state?.chatConfig?.enabled" in sidebar_js
    assert "Trợ lý dữ liệu" in sidebar_js


def test_export_block_downloads_through_api_helper():
    """Khối `export` (b10 D9) phải tải bằng API.download, không dùng link trực tiếp."""
    blocks_js = _read("js/components/chat-blocks.js")
    assert "API.download(" in blocks_js
    assert "/chat/exports/" in blocks_js
    assert "export: renderExport" in blocks_js


def test_report_sections_are_grouped_with_a_heading():
    blocks_js = _read("js/components/chat-blocks.js")
    assert "chat-section" in blocks_js and "chat-section-title" in blocks_js
    assert "sectionSlot(container, data.section)" in blocks_js


def test_budget_messages_are_shown():
    """b11 D9: dòng nhắc ``budget_warning`` và thông báo ``BUDGET_EXCEEDED`` kèm giờ reset."""
    blocks_js = _read("js/components/chat-blocks.js")
    chat_js = _read("js/pages/chat.js")
    assert "data.budget_warning" in blocks_js and "chat-budget-warning" in blocks_js
    assert "case 'BUDGET_EXCEEDED'" in chat_js and "formatResetTime(" in chat_js


def test_expired_session_opens_a_new_one():
    """b11 D10: phiên hết hạn → SESSION_NOT_FOUND → mở phiên mới và báo cho người dùng."""
    chat_js = _read("js/pages/chat.js")
    block = chat_js[chat_js.index("case 'SESSION_NOT_FOUND'") :]
    block = block[: block.index("break;")]
    assert "newSession" in block and "hết hạn" in block


@pytest.mark.skipif(NODE is None, reason="node không có trên máy")
def test_format_reset_time_in_node(tmp_path):
    script = tmp_path / "reset_time.js"
    script.write_text(
        "global.window = {}; global.document = {};\n"
        "require(process.argv[2]);\n"
        "const f = window.ChatBlocks.formatResetTime;\n"
        "console.log(JSON.stringify([f('2026-09-20T00:00:00+07:00'), f(''), f('bad')]));\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [NODE, str(script), str(STATIC / "js" / "components" / "chat-blocks.js")],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert json.loads(result.stdout) == ["00:00 20/09", "", ""]


def test_no_direct_links_to_chat_api():
    for rel in ("js/pages/chat.js", "js/components/chat-blocks.js", "js/app.js", "index.html"):
        text = _read(rel)
        assert 'href="/api/chat' not in text
        assert "window.open('/api/chat" not in text
        assert "/api/chat/exports" not in text


def test_chat_css_uses_prefixed_classes_and_theme_variables():
    css = re.sub(r"/\*.*?\*/", "", _read("css/chat.css"), flags=re.S)
    selectors = re.findall(r"(?m)^\s*([^{}@/][^{}]*)\{", css)
    for selector in selectors:
        for cls in re.findall(r"\.([A-Za-z][\w-]*)", selector):
            assert cls.startswith("chat-") or cls in {"active", "btn"}, cls
    hex_colors = re.findall(r"#[0-9a-fA-F]{3,8}\b", css)
    assert hex_colors == [], hex_colors


# ── Fixture event dùng chung cho fake server và thử thủ công ──

FIXTURE_FILES = sorted(FIXTURES.glob("ui_events_*.json"))


def test_fixtures_cover_every_event_type_and_block_kind():
    types, kinds = set(), set()
    for path in FIXTURE_FILES:
        for event in json.loads(path.read_text(encoding="utf-8"))["events"]:
            types.add(event["type"])
            if event["type"] == "data_block":
                kinds.add(event["data"]["kind"])
    assert {
        "status",
        "data_block",
        "commentary",
        "suggestions",
        "refusal",
        "clarify",
        "done",
        "error",
    } <= types
    assert {"kpi", "ranking", "table", "timeseries", "quote", "export"} <= kinds


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=[p.stem for p in FIXTURE_FILES])
def test_fixture_follows_event_protocol(path):
    events = json.loads(path.read_text(encoding="utf-8"))["events"]
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert events[-1]["type"] == "done"
    assert sum(1 for e in events if e["type"] == "done") == 1


# ── Hành vi chat-store.js chạy thật bằng node ──


STORE_SCRIPT = r"""
global.window = {};
require(process.argv[2]);
const S = window.ChatState;
const out = {};
S.trackAnswer('a1', { question: 'q' });
out.first = S.applyEvent('a1', { seq: 1, type: 'status' }).result;
out.dup = S.applyEvent('a1', { seq: 1, type: 'status' }).result;
out.gap = S.applyEvent('a1', { seq: 3, type: 'commentary' });
out.afterGapLast = S.getAnswer('a1').lastSeq;
out.resume = S.resumeMessages();
out.second = S.applyEvent('a1', { seq: 2, type: 'data_block' }).result;
out.late = S.applyEvent('a1', { seq: 3, type: 'done', data: { status: 'cancelled' } }).result;
out.unfinished = S.hasUnfinished();
out.doneStatus = S.getAnswer('a1').doneStatus;
out.events = S.getAnswer('a1').events.map(e => e.seq);
S.addPending('m1', 'hỏi', null);
out.pending = S.hasPending();
out.taken = S.takePending('m1');
out.takenAgain = S.takePending('m1');
S.setSessions(Array.from({ length: 60 }, (_, i) => ({ session_id: 's' + i })));
out.sessions = S.getSessions().length;
S.upsertSession({ session_id: 's5', title: 'x' });
out.firstSession = S.getSessions()[0].session_id;
S.setSessionId('s5'); S.removeSession('s5');
out.sessionAfterRemove = S.getSessionId();
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="node không có trên máy")
def test_chat_store_behaviour_in_node(tmp_path):
    script = tmp_path / "store_check.js"
    script.write_text(STORE_SCRIPT, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(script), str(STATIC / "js" / "components" / "chat-store.js")],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    out = json.loads(result.stdout)
    assert out["first"] == "applied"
    assert out["dup"] == "duplicate"
    assert out["gap"] == {"result": "gap", "lastSeq": 1, "needsResume": True}
    assert out["afterGapLast"] == 1
    assert out["resume"] == [{"type": "resume", "answer_id": "a1", "last_seq": 1}]
    assert out["second"] == "applied" and out["late"] == "applied"
    assert out["unfinished"] is False and out["doneStatus"] == "cancelled"
    assert out["events"] == [1, 2, 3]
    assert out["pending"] is True
    assert out["taken"] == {"question": "hỏi", "sessionId": None} and out["takenAgain"] is None
    assert out["sessions"] == 50
    assert out["firstSession"] == "s5"
    assert out["sessionAfterRemove"] is None
