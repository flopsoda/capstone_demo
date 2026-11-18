from datetime import date
from functools import partial

import pytest

from app.library.workflows.meeting_context_retrieval import retrieve_meeting_context as _retrieve_meeting_context
from app.library.workflows.meeting_store import Viewer
from app.systems.k_electronics import MEETING_STORE

pytestmark = pytest.mark.usefixtures("meeting_db")

EXECUTIVE = Viewer("ceo@example.com", "김민수", frozenset({"executive"}), 4, True)
STAFF = Viewer("staff@example.com", "박지훈", frozenset({"staff"}), 2, True)

# The workflow is system-neutral; these tests run it over K Electronics' meeting store (seeded from data/meetings.json).
retrieve_meeting_context = partial(_retrieve_meeting_context, store=MEETING_STORE, viewer=EXECUTIVE, today=date(2026, 10, 25))


def test_finds_neuroedge_strategy_meeting_only():
    out = retrieve_meeting_context(
        "뉴로엣지 인수와 관련해 지난 전략회의에서 결정한 기준과 아직 확인되지 않은 Action Item을 찾아줘.",
        ["NeuroEdge", "M&A", "AI NPU"],
    )
    assert out["matched"] == 1
    meeting = out["meetings"][0]
    assert meeting["date"] == "2026-09-15"
    assert meeting["title"] == "AI 반도체 전략회의"
    assert [d["speaker"] for d in meeting["decisions"]] == ["CFO", "CTO", "CEO"]
    assert meeting["decisions"][0]["ref"] == "MTG-2026-09-15-D1"


def test_returns_only_open_action_items():
    out = retrieve_meeting_context("NeuroEdge M&A", ["NeuroEdge"])
    items = out["meetings"][0]["open_action_items"]
    assert [a["text"] for a in items] == ["적정 기업가치 산정", "최대 고객사 의존도 확인", "핵심인력 Retention 확인"]
    first = items[0]
    assert (first["id"], first["owner"], first["due"], first["overdue_days"]) == ("MTG-2026-09-15-A1", "박지훈 (CFO)", "2026-10-10", 15)


def test_english_topic_hint_alone_matches():
    out = retrieve_meeting_context("past strategy meeting decisions", ["NeuroEdge acquisition"])
    assert [m["id"] for m in out["meetings"]] == ["MTG-2026-09-15"]


def test_unrelated_query_matches_other_meeting():
    out = retrieve_meeting_context("3나노 설비 투자 CAPEX 결정", ["설비 투자"])
    assert [m["id"] for m in out["meetings"]] == ["MTG-2026-08-27"]


def test_incidental_keyword_does_not_pull_in_other_meetings():
    # "투자" alone overlaps the CAPEX meeting's topics; it must not outrank the real match.
    out = retrieve_meeting_context(
        "뉴로엣지(NeuroEdge) 인수·제휴·투자 관련 경영회의 결정사항 및 후속 조건, 미완료 Action Item",
        ["NeuroEdge", "뉴로엣지", "M&A", "인수", "투자"],
    )
    assert [m["id"] for m in out["meetings"]] == ["MTG-2026-09-15"]


def test_no_match_returns_empty():
    out = retrieve_meeting_context("점심 메뉴", [])
    assert out["matched"] == 0 and out["meetings"] == []


def test_meetings_above_the_viewers_clearance_are_never_loaded():
    # The NeuroEdge strategy meeting is 대외비 (3): a 사내 (2) user cannot find it, however the query is phrased.
    out = retrieve_meeting_context("뉴로엣지 인수 전략회의", ["NeuroEdge"], viewer=STAFF)
    assert "MTG-2026-09-15" not in [m["id"] for m in out["meetings"]]
    assert retrieve_meeting_context("", [], viewer=STAFF, meeting_id="MTG-2026-09-15")["matched"] == 0


def test_one_meeting_by_id_and_by_date_range():
    out = retrieve_meeting_context("", [], meeting_id="MTG-2026-08-27")
    assert [m["id"] for m in out["meetings"]] == ["MTG-2026-08-27"]
    out = retrieve_meeting_context("NeuroEdge", ["NeuroEdge"], date_from="2026-10-01")
    assert out["matched"] == 0


def test_scenario_02_items_have_people_states_notes_and_reachable_owners(monkeypatch):
    """Final scenario 02: the 9/15 items as the 회장 hears them, and reminders that reach the two people asked about."""
    from app.library.workflows import outbox
    from app.library.workflows.action_items import track_action_items, update_action_item

    monkeypatch.setattr(outbox, "lookup_email", lambda name: None)  # no gateway directory locally
    monkeypatch.setenv("DEMO_MAIL_KELEC_CTO", "cto.demo@example.org")  # a team mailbox put in by the environment
    monkeypatch.delenv("DEMO_MAIL_KELEC_CFO", raising=False)
    monkeypatch.setenv("MEETINGS_CONFIRM_GROUPS", "*")
    today = date(2026, 11, 18)
    out = track_action_items(store=MEETING_STORE, viewer=EXECUTIVE, meeting_id="MTG-2026-09-15",
                             status=["open", "in_progress", "blocked", "done"], today=today)
    items = {i["text"]: (i["owner"], i["status"], i["overdue_days"] > 0, i["last_note"]) for i in out["items"]}
    assert items == {
        "적정 기업가치 산정": ("박지훈 (CFO)", "open", True, None),
        "핵심인력 Retention 확인": ("이서연 (CTO)", "blocked", True, "NeuroEdge 핵심 설계인력이 연구 자율성 보장을 요구해 유지 방안 협의가 지연되고 있음"),
        "최대 고객사 의존도 확인": ("전략기획팀", "in_progress", False, None),
        "NeuroEdge 기술 실사 자료 요청": ("이서연 (CTO)", "done", False, None),
    }
    group = Viewer(None, "", frozenset(), 4, False)  # the Group's request over A2A
    sent = [update_action_item(store=MEETING_STORE, viewer=group, item_id=f"MTG-2026-09-15-A{n}", remind=True, today=today)
            for n in (1, 3)]
    assert [(m["to"], m["email"], m["status"]) for s in sent for m in s["mail_queued"]] == [
        ("박지훈 (CFO)", "jihoon.park@kelec.example.com", "approved"), ("이서연 (CTO)", "cto.demo@example.org", "approved")]
    assert [s["item"]["status"] for s in sent] == ["open", "blocked"]  # reminding changes nothing on the items
