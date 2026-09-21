from __future__ import annotations

import json

import pytest

from dms.chat.ai.answer_events import (
    EVENT_VERSION,
    AnswerEvent,
    CommentaryStatus,
    DoneStatus,
    EventType,
    ListSink,
    Stage,
)


def test_event_serializes_to_json():
    event = AnswerEvent(seq=3, type=EventType.DATA_BLOCK, data={"block_id": "b1", "kind": "kpi"})

    payload = event.to_dict()
    text = json.dumps(payload, ensure_ascii=False)

    assert payload == {"seq": 3, "type": "data_block", "data": {"block_id": "b1", "kind": "kpi"}}
    assert json.loads(text)["type"] in {member.value for member in EventType}


def test_event_type_set_is_exactly_the_protocol():
    assert {member.value for member in EventType} == {
        "status",
        "data_block",
        "commentary",
        "suggestions",
        "refusal",
        "clarify",
        "done",
        "error",
    }
    assert EVENT_VERSION == 1


def test_done_and_commentary_status_values():
    assert {member.value for member in DoneStatus} == {
        "ok",
        "partial",
        "no_data",
        "refused",
        "clarify",
        "help",
        "not_supported",
        "error",
        "cancelled",
    }
    assert {member.value for member in CommentaryStatus} == {
        "full",
        "partial",
        "unavailable",
        "skipped",
    }
    assert {member.value for member in Stage} == {
        "understanding",
        "planning",
        "querying",
        "writing",
    }


def test_event_data_is_copied_so_callers_cannot_mutate_it():
    data = {"text": "xin chào"}
    event = AnswerEvent(seq=1, type=EventType.COMMENTARY, data=data)

    data["text"] = "đã đổi"

    assert event.to_dict()["data"]["text"] == "đã đổi"  # cùng dict gốc
    snapshot = event.to_dict()
    snapshot["data"]["text"] = "chỉ đổi bản sao"
    assert event.data["text"] == "đã đổi"


def test_list_sink_collects_and_reports_cancellation():
    sink = ListSink()
    sink.emit(AnswerEvent(seq=1, type=EventType.STATUS, data={"stage": "querying"}))
    sink.emit(AnswerEvent(seq=2, type=EventType.DONE, data={"status": "ok"}))

    assert sink.types() == ["status", "done"]
    assert sink.is_cancelled() is False
    assert sink.done is not None and sink.done.data["status"] == "ok"
    assert sink.of_type(EventType.STATUS)[0].seq == 1
    assert sink.to_list()[0]["seq"] == 1

    sink.cancel()
    assert sink.is_cancelled() is True


@pytest.mark.parametrize("event_type", list(EventType))
def test_every_event_type_serializes(event_type):
    assert AnswerEvent(seq=1, type=event_type).to_dict()["type"] == event_type.value
