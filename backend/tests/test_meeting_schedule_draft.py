"""A booking request checked before it is booked (meeting_schedule action=draft): what the records fill, what is missing."""

from __future__ import annotations

from datetime import date

from app.library.workflows.meeting_minutes import save_minutes
from app.library.workflows.meeting_schedule import meeting_schedule
from app.library.workflows.meeting_store import Viewer
from app.systems.k_electronics import MEETING_STORE as STORE

TODAY = date(2026, 10, 25)
SECRETARY = Viewer("secretary@example.com", "이서연", frozenset({"secretary"}), 3, True)


def held(**overrides):
    minutes = dict(
        title="공급망 점검회의", date="2026-10-24", summary="2차 협력사 단가 조정안을 검토했다.", classification=2,
        location="본사 7층 회의실",
        attendees=[{"name": "김민수", "title": "CEO", "role": "chair"}, {"name": "박지훈", "title": "CFO"},
                   {"name": "김영호", "title": "CTO"}],
        decisions=[{"kind": "issue", "statement": "협력사 재고 기준을 몇 주로 할지 정하지 못했다"}],
        action_items=[{"text": "협력사별 재고 현황 보고", "owner": "박지훈", "due": "2026-10-28"}],
        finalize=True,
    )
    return save_minutes(store=STORE, viewer=SECRETARY, **{**minutes, **overrides})


def draft(**kw):
    return meeting_schedule(store=STORE, viewer=SECRETARY, action="draft", today=TODAY, **kw)


def test_a_vague_request_is_filled_from_the_last_meeting_of_the_subject_and_asks_for_the_rest(meeting_db):
    prev = held()
    d = draft(title="공급망 회의", starts_at="2026-10-27")  # "월요일에 공급망 회의 잡아줘" — no time, no one named
    assert d["missing"] == ["start"] and d["complete"] is False
    assert d["previous"]["id"] == prev["meeting_id"]
    form = d["draft"]
    assert form["date"] == "2026-10-27" and form["start"] is None and form["location"] == "본사 7층 회의실"
    assert {a["name"] for a in form["attendees"]} == {"김민수", "박지훈", "김영호"}  # the last meeting's attendees, said so
    assert any("참석자" in a for a in d["assumed"]) and any("장소" in a for a in d["assumed"])
    assert form["carry_over_from"] == prev["meeting_id"] and form["carried"] == ["협력사 재고 기준을 몇 주로 할지 정하지 못했다"]
    assert [s["text"] for s in d["suggestions"]["agenda"]] == ["협력사별 재고 현황 보고"]  # open work, offered not added
    with STORE.connect() as conn:  # nothing was booked
        assert conn.execute("SELECT count(*) AS n FROM meetings WHERE status = 'scheduled'").fetchone()["n"] == 0


def test_names_that_fit_several_people_or_nobody_are_asked_about(meeting_db):
    held()
    d = draft(title="공급망 회의", starts_at="2026-10-27 14:00", attendees=["김", "박 CFO", "최대리"])
    who = {a["input"]: a for a in d["draft"]["attendees"]}
    assert who["김"]["status"] == "ambiguous" and {c["label"] for c in who["김"]["candidates"]} == {"김민수 (CEO)", "김영호 (CTO)"}
    assert who["박 CFO"]["status"] == "found" and who["박 CFO"]["name"] == "박지훈" and who["박 CFO"]["guessed"]
    assert who["최대리"]["status"] == "new"
    assert d["unclear"] == ["김", "최대리"] and d["draft"]["end"] == "15:00" and d["complete"] is False


def test_an_overlapping_booking_is_flagged(meeting_db):
    held()
    meeting_schedule(store=STORE, viewer=SECRETARY, action="create", title="예산 회의", starts_at="2026-10-27 14:30",
                     ends_at="2026-10-27 15:30", attendees=["박지훈 (CFO)"], today=TODAY)
    d = draft(title="공급망 회의", starts_at="2026-10-27 14:00", attendees=["김민수 (CEO)", "박지훈 (CFO)"])
    assert len(d["conflicts"]) == 1 and "예산 회의" in d["conflicts"][0] and "박지훈" in d["conflicts"][0]
    assert d["complete"] is False


def test_a_request_that_says_everything_can_be_booked_as_it_is(meeting_db):
    held()
    d = draft(title="공급망 회의", starts_at="2026-10-27 14:00", ends_at="2026-10-27 15:00",
              attendees=["김민수 (CEO, 의장)", "박지훈 (CFO)"])
    assert d["complete"] is True and d["missing"] == [] and d["conflicts"] == []
    assert [a["role"] for a in d["draft"]["attendees"]] == ["chair", None]
