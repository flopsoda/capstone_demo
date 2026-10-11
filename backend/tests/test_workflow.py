from app.workflows.meeting_context_retrieval import (
    WORKFLOW_NAME,
    retrieve_meeting_context,
    run_meeting_context_retrieval,
)


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


def test_returns_only_open_action_items():
    out = retrieve_meeting_context("NeuroEdge M&A", ["NeuroEdge"])
    items = [a["text"] for a in out["meetings"][0]["open_action_items"]]
    assert items == ["적정 기업가치 산정", "최대 고객사 의존도 확인", "핵심인력 Retention 확인"]


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


def test_tool_metadata():
    assert run_meeting_context_retrieval.name == WORKFLOW_NAME
    result = run_meeting_context_retrieval.invoke({"query": "뉴로엣지 인수", "topics": ["M&A"]})
    assert result["matched"] == 1
