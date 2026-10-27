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
    assert (first["id"], first["owner"], first["due"], first["overdue_days"]) == ("MTG-2026-09-15-A1", "재무기획팀", "2026-10-10", 15)


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
