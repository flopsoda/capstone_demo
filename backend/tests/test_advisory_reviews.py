"""The Advisory redesign end to end on the meeting store (needs MEETINGS_TEST_DATABASE_URL): the advisory agent consults
the meeting agent itself (look-up only), computes, records a checked judgement, and hands back a report reference that
travels K → Group over A2A as a reference; the PDF opens through the report endpoints under the store's access rule.
A scripted chat model stands in for the LLM (shared by every agent, calls are sequential)."""

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.library.workflows.advisory import report
from app.library.workflows.meeting_store import KST, Viewer
from app.runtime import agent_core
from app.runtime.events import new_run, stream_run
from app.runtime.schemas import Principal
from app.systems.group.app import app as group_app
from app.systems.group.tools import request_k_electronics_review as a2a_tool
from app.systems.k_electronics import ADVISORY_STORE
from app.systems.k_electronics.agents import advisory as k_advisory
from app.systems.k_electronics.app import app as k_app

CEO = Principal(email="ceo@example.com", name="김민수", groups=("executive",), clearance=4)
STAFF = Principal(email="staff@example.com", name="박사원", groups=("staff",), clearance=2)


class _Model:
    def __init__(self, turns):
        self.turns = list(turns)
        self.seen = []

    def bind_tools(self, _tools):
        return self

    def stream(self, messages):
        self.seen.append(list(messages))
        yield self.turns.pop(0)


def call(name, idx, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{idx}"}])


def review_id(n: int = 1) -> str:
    """Judgement ids are per day in a fresh store (the test DB is new for every test)."""
    return f"ADV-{datetime.now(KST).date().isoformat()}-{n:02d}"


def core_and_details(**changes):
    """A judgement split as the agent records it: the core, then the three views (add_advisory_details)."""
    o = judgement(**changes)
    return {k: v for k, v in o.items() if k != "personas"}, {"personas": o["personas"]}


def judgement(**changes):
    o = {"company": "뉴로엣지", "evaluation_type": "initial", "price_trillion_krw": 2.8, "recommendation": "CONDITIONAL_PROCEED",
         "headline": "최초 요구가 2.8조 원은 회의 기준 2.5조 원을 넘어 가격 조정을 전제로 조건부 진행",
         "summary": "엣지 NPU 역량은 필요하지만 가격이 DCF 주주가치보다 30.3% 높다.",
         "rationale": [{"statement": "회의 기준 초과, 프리미엄 30.3%", "evidence": ["MTG-2026-09-15-D1", "A1.check.price_ceiling", "A1.price.premium_vs_dcf"]}],
         "personas": [
             {"persona": "CEO", "stance": "CONDITIONAL_PROCEED", "judgement": "시장 진입 단축 가치가 크다.", "key_points": [{"statement": "24~30개월", "evidence": ["A1.tech.ttm_gain"]}]},
             {"persona": "CFO", "stance": "CONDITIONAL_PROCEED", "judgement": "가격 조정이 전제다.", "key_points": [{"statement": "DCF", "evidence": ["A1.dcf.equity_value"]}]},
             {"persona": "CTO", "stance": "CONDITIONAL_PROCEED", "judgement": "핵심인력 잔류가 막혀 있다.", "key_points": [{"statement": "Retention 막힘", "evidence": ["A1.people.retention_open", "MTG-2026-09-15-A3"]}]}],
         "conditions": [{"condition": "가격 조정", "addresses": "가격", "evidence": ["A1.price.premium_vs_dcf"]}],
         "report": True}
    o.update(changes)
    return o


MEETING_LOOKUP = [  # the meeting agent's own turns, inside the advisory agent's handoff
    call("run_meeting_context_retrieval", 2, query="뉴로엣지 인수 기준", topics=["NeuroEdge"]),
    AIMessage("MTG-2026-09-15-D1: 2.5조 원 초과 시 가격 재검토 · MTG-2026-09-15-A3 핵심인력 Retention 확인(막힘)"),
]


def run_advisory(model, monkeypatch, request="뉴로엣지 인수 타당성을 종합 평가하고 보고서도 줘", principal=CEO, origin="direct"):
    monkeypatch.setattr(agent_core, "get_chat_model", lambda: model)
    run = new_run(system="k_electronics", root_agent="advisory", origin=origin, principal=principal)
    return list(stream_run(lambda: k_advisory.run_advisory_agent(request, invocation="direct"), run=run, hub=None))


def results(events, tool):
    return [e["result"] for e in events if e["type"] == "tool_end" and e["tool"] == tool]


def test_the_advisory_agent_investigates_judges_records_and_offers_a_report(meeting_db, monkeypatch):
    model = _Model([
        call("ask_meeting_agent", 1, task="뉴로엣지 인수 관련 회의 기준과 후속 업무, 결정 ref 포함"),
        *MEETING_LOOKUP,
        call("run_mna_review", 3, query="뉴로엣지 인수 타당성", company="뉴로엣지",
             criteria={"price_ceiling_trillion_krw": 2.5, "min_time_to_market_gain_months": 24}),
        call("record_advisory_opinion", 4, **core_and_details(summary="DCF 주주가치는 3조 1,000억 원이다.")[0]),  # a number not in the evidence
        call("record_advisory_opinion", 5, **core_and_details()[0]),  # fixed
        call("add_advisory_details", 6, review_id=review_id(), **core_and_details()[1]),  # the three views → the report
        AIMessage("조건부 진행 — 투자심의 보고서(ADV)를 첨부했다."),
    ])
    events = run_advisory(model, monkeypatch)
    assert events[-1]["type"] == "done", events[-1]

    # the advisory agent asked the meeting agent itself (a handoff, not a tool call), look-up only
    starts = [e for e in events if e["type"] == "delegation_start"]
    assert [(e["from"], e["to"]) for e in starts] == [("advisory", "meeting")]
    assert not [e for e in events if e["type"] == "tool_start" and e["tool"] == "ask_meeting_agent"]

    first, second = results(events, "record_advisory_opinion")
    assert first["accepted"] is False and any("3조 1,000억" in p for p in first["problems"])
    assert second["accepted"] is True and second["recommendation"] == "CONDITIONAL_PROCEED"  # the model's recommendation, kept as given
    details = results(events, "add_advisory_details")[0]
    assert details["accepted"] is True and details["review_id"] == second["review_id"]
    ref = details["artifacts"][0]
    assert ref["kind"] == "report" and ref["id"] == second["review_id"] and ref["system"] == "k_electronics"
    assert ref["recommendation_label"] == "조건부 진행" and "content" not in ref  # a reference, never the file

    kept = ADVISORY_STORE.get(Viewer("ceo@example.com", "", frozenset({"executive"}), 4, True), second["review_id"])
    assert kept["classification"] == 3 and kept["meeting_codes"] == ["MTG-2026-09-15"]  # it draws on a 대외비 meeting
    assert "MTG-2026-09-15-D1" in kept["evidence"]["items"]  # read by the meeting agent, cited, kept with the judgement
    assert {v["persona"] for v in kept["opinion"]["personas"]} == {"CEO", "CFO", "CTO"}  # the second part merged in
    assert kept["evidence"]["items"]["MTG-2026-09-15-D1"]["sources"] == ["run_meeting_context_retrieval"]
    # someone whose clearance does not cover that meeting sees no judgement at all
    assert ADVISORY_STORE.get(Viewer("staff@example.com", "", frozenset({"staff"}), 2, True), second["review_id"]) is None


def test_a_reevaluation_reads_the_prior_judgement_and_cites_what_changed(meeting_db, monkeypatch):
    first = _Model([
        call("ask_meeting_agent", 1, task="뉴로엣지 회의 기준"), *MEETING_LOOKUP,
        call("run_mna_review", 3, query="평가", company="뉴로엣지", criteria={"price_ceiling_trillion_krw": 2.5}),
        call("record_advisory_opinion", 4, **core_and_details(report=False)[0]),
        AIMessage("조건부 진행"),
    ])
    prior_id = results(run_advisory(first, monkeypatch), "record_advisory_opinion")[0]["review_id"]

    again = _Model([
        call("lookup_advisory_reviews", 1, company="뉴로엣지"),
        call("run_mna_review", 2, query="재평가", company="뉴로엣지", criteria={"price_ceiling_trillion_krw": 2.5},
             scenarios=[{"name": "알파모바일 매출 절반 상실", "largest_customer_revenue_loss": 0.5}]),
        call("record_advisory_opinion", 3, **core_and_details(
            evaluation_type="reevaluation", recommendation="DO_NOT_PROCEED", prior_id=prior_id, report=False,
            headline="알파모바일 매출 절반만 잃어도 DCF 주주가치가 19.3% 줄어 보류",
            rationale=[{"statement": "주주가치 19.3% 감소, 이전 판단의 가치 대비 하락",
                        "evidence": ["A1.S1.delta.equity_value_pct", f"{prior_id}:A1.dcf.equity_value", "A1.check.price_ceiling"]}],
            changes_from_prior="이전 조건부 진행 이후 최대 고객 매출 감소를 반영하니 DCF 주주가치가 21,493억 원에서 17,343억 원으로 줄었다.")[0]),
        AIMessage("보류로 변경"),
    ])
    events = run_advisory(again, monkeypatch, request="새 소식이 있다니 뉴로엣지 인수를 다시 평가해줘")
    looked = results(events, "lookup_advisory_reviews")[0]
    assert looked["found"] and looked["reviews"][0]["review_id"] == prior_id and looked["reviews"][0]["recommendation_label"] == "조건부 진행"
    kept = results(events, "record_advisory_opinion")[0]
    assert kept["accepted"] is True and kept["prior_review"] == prior_id and kept["recommendation"] == "DO_NOT_PROCEED"


@pytest.mark.parametrize("origin", ["direct", "a2a"])  # not even what an A2A request may do
def test_a_meeting_lookup_for_the_advisory_agent_cannot_send_mail(meeting_db, monkeypatch, origin):
    model = _Model([
        call("ask_meeting_agent", 1, task="핵심인력 Retention 진행 상황"),
        call("update_action_item", 2, item_id="MTG-2026-09-15-A3", remind=True),  # the meeting agent tries to mail the owner
        AIMessage("리마인더는 보낼 수 없었다"),
        AIMessage("조회 결과만 반영"),
    ])
    events = run_advisory(model, monkeypatch, origin=origin)
    refused = results(events, "update_action_item")[0]
    assert refused["updated"] is False and "조회만 맡은" in refused["error"]


def test_the_report_reference_travels_k_to_group_and_the_pdf_opens_through_the_group(meeting_db, monkeypatch):
    model = _Model([
        call("request_k_electronics_review", 1, request="뉴로엣지 인수 타당성을 종합 평가하고 투자심의 보고서를 달라"),
        call("delegate_to_advisory_agent", 2, task="뉴로엣지 인수 종합 평가와 투자심의 보고서"),
        call("run_mna_review", 3, query="종합 평가", company="뉴로엣지"),
        call("record_advisory_opinion", 4, **core_and_details(
            headline="최초 요구가 2.8조 원은 DCF 주주가치보다 30.3% 높아 조건부 진행",
            rationale=[{"statement": "프리미엄 30.3%", "evidence": ["A1.price.premium_vs_dcf"]}])[0]),
        call("add_advisory_details", 5, review_id=review_id(), personas=[
            {"persona": "CEO", "stance": "CONDITIONAL_PROCEED", "judgement": "시장 진입 단축", "evidence": ["A1.tech.ttm_gain"]},
            {"persona": "CFO", "stance": "CONDITIONAL_PROCEED", "judgement": "가격 조정", "evidence": ["A1.dcf.equity_value"]},
            {"persona": "CTO", "stance": "CONDITIONAL_PROCEED", "judgement": "인력 잔류", "evidence": ["A1.people.retention_open"]}]),
        AIMessage("경영자문: 조건부 진행, 보고서 첨부"),
        AIMessage("K전자 회신: 조건부 진행"),
        AIMessage("그룹 보고: 조건부 진행, 상세는 첨부 보고서"),
    ])
    monkeypatch.setattr(agent_core, "get_chat_model", lambda: model)
    monkeypatch.setattr(a2a_tool, "make_client", lambda: TestClient(k_app))
    with TestClient(group_app).stream("POST", "/api/agents/group-super/run", json={"request": "뉴로엣지 인수 검토해줘"}) as res:
        events = [json.loads(line) for line in res.iter_lines() if line.strip()]
    assert events[-1]["type"] == "done", events[-1]
    end = next(e for e in events if e["type"] == "a2a_end")
    ref = end["artifacts"][0]
    assert ref["kind"] == "report" and ref["system"] == "k_electronics" and ref["id"].startswith("ADV-")
    # the Group Super got K's answer plus one line naming the report (picked out in code, not copied by a model)
    tool_message = model.seen[-1][-1].content
    assert tool_message.startswith("K전자 회신: 조건부 진행") and f"[첨부 보고서]" in tool_message and ref["id"] in tool_message

    group = TestClient(group_app)
    res = group.post("/api/reports", json={"system": "k_electronics", "id": ref["id"]})  # the Group fetches it from K over HTTP
    if report.fonts_available():
        assert res.status_code == 200 and res.headers["content-type"] == "application/pdf" and res.content.startswith(b"%PDF")
        assert "filename*=UTF-8''" in res.headers["content-disposition"]
    else:
        assert res.status_code == 503 and "글꼴" in res.json()["error"]
    # a user whose clearance does not cover the meetings behind it (none here: the judgement cites no meeting) …
    assert group.post("/api/reports", json={"system": "k_electronics", "id": "ADV-1999-01-01-01"}).status_code == 404
    assert group.post("/api/reports", json={"system": "nowhere", "id": ref["id"]}).status_code == 404


def test_a_judgement_behind_a_classified_meeting_does_not_open_for_a_lower_clearance(meeting_db, monkeypatch):
    model = _Model([
        call("ask_meeting_agent", 1, task="회의 기준"), *MEETING_LOOKUP,
        call("run_mna_review", 3, query="평가", company="뉴로엣지", criteria={"price_ceiling_trillion_krw": 2.5}),
        call("record_advisory_opinion", 4, **core_and_details()[0]),
        AIMessage("완료"),
    ])
    code = results(run_advisory(model, monkeypatch), "record_advisory_opinion")[0]["review_id"]
    k = TestClient(k_app)
    staff = k.post("/api/a2a/reports", json={"id": code, "principal": STAFF.model_dump()})
    assert staff.status_code == 404  # clearance 2 < the 대외비(3) meeting it draws on
    ceo = k.post("/api/a2a/reports", json={"id": code, "principal": CEO.model_dump()})
    assert ceo.status_code == (200 if report.fonts_available() else 503)


def test_an_answer_that_leaves_a_refused_record_unfixed_is_sent_back_once(meeting_db, monkeypatch):
    model = _Model([
        call("run_mna_review", 1, query="평가", company="뉴로엣지"),
        call("record_advisory_opinion", 2, **core_and_details(
            headline="DCF 주주가치는 9조 원", rationale=[{"statement": "프리미엄", "evidence": ["A1.price.premium_vs_dcf"]}])[0]),  # refused: 9조
        AIMessage("조건부 진행입니다."),  # tries to finish with the refused judgement → asked again
        call("record_advisory_opinion", 3, **core_and_details(
            headline="최초 요구가 2.8조 원은 DCF 주주가치보다 30.3% 높다", rationale=[{"statement": "프리미엄 30.3%", "evidence": ["A1.price.premium_vs_dcf"]}])[0]),
        AIMessage("조건부 진행 (기록됨)"),
    ])
    events = run_advisory(model, monkeypatch, request="뉴로엣지 인수 평가")
    refused, kept = results(events, "record_advisory_opinion")
    assert refused["accepted"] is False and kept["accepted"] is True
    assert "판단 기록이 거부된 채" in model.seen[3][-1].content  # the note the model got before it fixed the record
    assert events[-2]["response"] == "조건부 진행 (기록됨)"
