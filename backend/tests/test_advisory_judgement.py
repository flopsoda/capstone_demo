"""The Advisory redesign, offline: numbers computed from the data (no recommendation), the checks a judgement must pass
before it is kept (cited ids, numbers, unmet criteria, perspectives), the evidence ledger, and the look-up-only
handoffs the advisory agent uses to consult the meeting / news agents."""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.library.tools import evidence_items
from app.library.tools import delegation
from app.library.tools.meeting_access import current_viewer
from app.library.workflows.advisory import analysis, opinion as op
from app.runtime import agent_core, evidence
from app.runtime import events as runtime_events
from app.runtime.events import collect_events, new_run
from app.runtime.schemas import Principal

K_DATA = Path(__file__).resolve().parents[1] / "app/systems/k_electronics/data/companies.json"
G_DATA = Path(__file__).resolve().parents[1] / "app/systems/group/data/companies.json"
CRITERIA = {"price_ceiling_trillion_krw": 2.5, "min_time_to_market_gain_months": 24}
MEETING = {"meetings": [{"id": "MTG-2026-09-15", "date": "2026-09-15", "title": "AI 반도체 전략회의", "classification": 3,
                         "decisions": [{"ref": "MTG-2026-09-15-D1", "speaker": "CFO", "statement": "2.5조 원을 초과할 경우 가격 타당성 재검토"}],
                         "open_action_items": [{"id": "MTG-2026-09-15-A3", "text": "핵심인력 Retention 확인", "status": "blocked"}]}]}
NEWS = {"risks": [{"label": "고객 집중도", "level": "HIGH", "headline": "알파모바일, 뉴로엣지와 차세대 NPU 공급계약 중단 발표",
                   "summary": "알파모바일은 뉴로엣지 매출의 46%를 차지한다.", "source": {"id": "NEWS-2026-11-17-01", "date": "2026-11-17", "name": "반도체경제 (가상)"}}]}


@contextmanager
def run_context(run):
    token = runtime_events._run.set(run)
    try:
        yield run
    finally:
        runtime_events._run.reset(token)


def figures(result):
    return {i["id"]: i for i in result["items"]}


# ── numbers ─────────────────────────────────────────────────────────────────


def test_the_value_is_computed_from_the_cash_flow_plan_and_no_recommendation_is_made():
    out = analysis.analyze("뉴로엣지", path=K_DATA, criteria=CRITERIA)
    assert out["found"] and out["synthetic"] and "recommendation" not in json.dumps(out, ensure_ascii=False).split('"note"')[0]
    f = figures(out)
    c = analysis.find_company("뉴로엣지", K_DATA)
    rows = [r for r in c["financials"]["years"] if r["year"] > 2026]
    pv = sum(r["fcf"] / 1.11 ** (r["year"] - 2026) for r in rows) + rows[-1]["fcf"] * 1.03 / 0.08 / 1.11 ** 5
    assert f["A1.dcf.enterprise_value"]["value"] == pytest.approx(pv, abs=0.1)
    assert f["A1.dcf.equity_value"]["value"] == pytest.approx(pv - (980 + 242 - 740), abs=0.1)
    assert f["A1.price.premium_vs_dcf"]["value"] == pytest.approx((28000 / (pv - 482) - 1) * 100, abs=0.05)
    grid = out["tables"]["sensitivity"]["rows"]
    assert grid[0]["values"][1] > grid[1]["values"][1] > grid[2]["values"][1]  # a higher WACC, a lower value
    # the meeting's criteria are compared, not judged
    assert {c["id"]: c["met"] for c in out["checks"]} == {"A1.check.price_ceiling": False, "A1.check.time_to_market": True}
    assert any("변경지배권" in o for o in out["open_items"]) and any("3명" in o for o in out["open_items"])


def test_changing_the_data_changes_the_value(tmp_path):
    data = json.loads(K_DATA.read_text(encoding="utf-8"))
    data["companies"][0]["financials"]["years"][-1]["fcf"] = 1300  # half the last year's cash flow
    path = tmp_path / "companies.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert figures(analysis.analyze("뉴로엣지", path=path))["A1.dcf.equity_value"]["value"] < \
        figures(analysis.analyze("뉴로엣지", path=K_DATA))["A1.dcf.equity_value"]["value"]


def test_losing_the_largest_customer_flows_through_revenue_cash_flow_value_and_orders():
    out = analysis.analyze("뉴로엣지", path=K_DATA, scenarios=[{"name": "절반", "largest_customer_revenue_loss": 0.5},
                                                               {"name": "전부", "largest_customer_revenue_loss": 1.0}])
    f = figures(out)
    base, half, full = (f[k]["value"] for k in ("A1.dcf.equity_value", "A1.S1.dcf.equity_value", "A1.S2.dcf.equity_value"))
    assert base > half > full
    assert f["A1.S1.delta.equity_value"]["value"] == pytest.approx(half - base, abs=0.2)
    # 2027: revenue × the customer's 2027 share (40%) × loss
    assert f["A1.S2.cust.lost_revenue_next"]["value"] == pytest.approx(5100 * 0.40, abs=0.1)
    assert f["A1.S2.cust.po_coverage"]["value"] == pytest.approx((2215 - 1120) / 5100 * 100, abs=0.05)
    assert f["A1.S2.plan.break_even_year"]["value"] > f["A1.plan.break_even_year"]["value"]
    assert out["scenarios"][1]["notes"][0].startswith("알파모바일 매출의 100%")


def test_a_price_and_other_drivers_are_scenarios_of_the_same_model():
    out = analysis.analyze("뉴로엣지", path=K_DATA, price_trillion_krw=2.2,
                           scenarios=[{"name": "할인율 상승", "wacc": 0.12}, {"name": "특허 손실", "ip_loss": 1000}])
    f = figures(out)
    assert out["price"]["label"] == "제안가" and f["A1.deal.price"]["display"] == "2.2조 원"
    assert f["A1.S1.dcf.equity_value"]["value"] < f["A1.dcf.equity_value"]["value"]
    assert f["A1.S2.dcf.equity_value"]["value"] == pytest.approx(f["A1.dcf.equity_value"]["value"] - 1000, abs=0.2)
    assert analysis.analyze("뉴로엣지", path=K_DATA, scenarios=[{"name": "x", "largest_customer_revenue_loss": 2}])["error"]


def test_a_company_with_less_data_still_gets_its_numbers():
    out = analysis.analyze("브라이트셀", path=G_DATA)
    f = figures(out)
    assert out["found"] and f["A1.dcf.equity_value"]["value"] > 0 and "A1.people.retention_open" not in f
    assert f["A1.plan.break_even_year"]["value"] == "흑자 유지"
    assert set(out["persona_lenses"]) == {"CEO", "CFO", "CTO"}


# ── the judgement and its checks ────────────────────────────────────────────


def ledger_with(*, news=False, price=None):
    led = evidence.Ledger()
    token = evidence._LEDGER.set(led)
    try:
        evidence_items.recorded(MEETING, evidence_items.meeting_context)
        if news:
            evidence_items.recorded(NEWS, evidence_items.risk_news)
        a = analysis.analyze("뉴로엣지", path=K_DATA, criteria=CRITERIA, price_trillion_krw=price,
                             scenarios=[{"name": "절반", "largest_customer_revenue_loss": 0.5}] if news else [])
        led.analyses.append(a)
        evidence.add_many(evidence_items.analysis(a))
    finally:
        evidence._LEDGER.reset(token)
    return led


def judgement(**changes):
    o = {"company": "뉴로엣지", "evaluation_type": "initial", "price_trillion_krw": 2.8, "recommendation": "CONDITIONAL_PROCEED",
         "headline": "2.8조 원은 DCF 주주가치보다 30.3% 높아 가격 조정을 전제로 조건부 진행",
         "summary": "전략 가치는 크나 가격이 회의 기준 2.5조 원을 넘는다.",
         "rationale": [{"statement": "프리미엄 30.3%, 회의 기준 초과", "evidence": ["A1.price.premium_vs_dcf", "A1.check.price_ceiling", "MTG-2026-09-15-D1"]}],
         "personas": [{"persona": "CEO", "stance": "CONDITIONAL_PROCEED", "judgement": "Time-to-Market 단축 가치가 크다.", "key_points": [{"statement": "24~30개월 단축", "evidence": ["A1.tech.ttm_gain"]}]},
                      {"persona": "CFO", "stance": "CONDITIONAL_PROCEED", "judgement": "DCF 주주가치 21,493억 원 대비 가격이 높다.", "key_points": [{"statement": "프리미엄", "evidence": ["A1.dcf.equity_value"]}]},
                      {"persona": "CTO", "stance": "CONDITIONAL_PROCEED", "judgement": "핵심 설계자 잔류가 전제다.", "key_points": [{"statement": "3명 미합의", "evidence": ["A1.people.retention_open", "MTG-2026-09-15-A3"]}]}],
         "conditions": [{"condition": "가격 조정", "addresses": "가격 프리미엄", "evidence": ["A1.price.premium_vs_dcf"]}]}
    o.update(changes)
    return o


def test_a_grounded_judgement_passes():
    assert op.check(judgement(), ledger_with()) == []


def test_ids_that_were_not_read_in_the_run_are_refused():
    problems = op.check(judgement(rationale=[{"statement": "근거", "evidence": ["A1.check.price_ceiling", "NEWS-2099-01-01-01"]}]), ledger_with())
    assert any("NEWS-2099-01-01-01" in p for p in problems)


def test_a_number_that_is_not_in_the_evidence_is_refused():
    problems = op.check(judgement(summary="DCF 주주가치는 3조 4,000억 원이고 프리미엄은 12.7%다."), ledger_with())
    assert any("3조 4,000억" in p and "12.7%" in p for p in problems)
    # differences and ratios of two figures of the evidence are fine (2.8조 − 2.5조 = 3,000억)
    assert op.check(judgement(summary="가격이 회의 기준보다 3,000억 원 높다."), ledger_with()) == []


def test_an_unmet_meeting_criterion_must_be_addressed():
    problems = op.check(judgement(rationale=[{"statement": "프리미엄 30.3%", "evidence": ["A1.price.premium_vs_dcf"]}]), ledger_with())
    assert any("A1.check.price_ceiling" in p for p in problems)


def test_each_perspective_rests_on_its_own_figures_and_a_condition_states_its_conditions():
    core = judgement()
    details = {"personas": [dict(p) for p in core["personas"]]}
    assert op.check_details(details, ledger_with(), core) == []
    details["personas"][2]["key_points"] = [{"statement": "가격", "evidence": ["A1.price.premium_vs_dcf"]}]  # a CFO figure for the CTO
    assert any("CTO 관점" in p for p in op.check_details(details, ledger_with(), core))
    assert any("CEO" in p for p in op.check_details({"personas": details["personas"][1:]}, ledger_with(), core))
    assert any("수치" in p for p in op.check_details({**details, "risks": [{"title": "x", "level": "HIGH", "assessment": "손실 9,999억 원"}]},
                                                      ledger_with(), core))
    assert any("conditions" in p for p in op.check(judgement(conditions=[]), ledger_with()))


def test_the_judgements_price_must_have_been_computed():
    assert any("price_trillion_krw" in p for p in op.check(judgement(price_trillion_krw=2.0), ledger_with()))
    assert op.check(judgement(price_trillion_krw=2.2, headline="제안가 기준 판단", rationale=[
        {"statement": "회의 기준 충족", "evidence": ["A1.check.price_ceiling"]}]), ledger_with(price=2.2)) == []


def test_a_reevaluation_names_its_prior_and_what_changed():
    o = judgement(evaluation_type="reevaluation", recommendation="DO_NOT_PROCEED",
                  rationale=[{"statement": "공급계약 중단", "evidence": ["NEWS-2026-11-17-01", "A1.S1.delta.equity_value", "A1.check.price_ceiling"]}])
    problems = op.check(o, ledger_with(news=True))
    assert any("prior_id" in p for p in problems) and any("changes_from_prior" in p for p in problems)
    ok = op.check({**o, "prior_id": "ADV-1", "changes_from_prior": "주주가치가 19.3% 줄었다."}, ledger_with(news=True), prior={"code": "ADV-1"})
    assert ok == []


def test_the_kept_copy_holds_the_cited_records_and_every_figure():
    led = ledger_with()
    snap = op.snapshot(judgement(), led)
    assert "MTG-2026-09-15-D1" in snap["items"] and "A1.dcf.enterprise_value" in snap["items"]  # cited / every figure
    assert op.meeting_codes(snap) == ["MTG-2026-09-15"] and op.evidence_as_of(snap) == "2026-11-10"


# ── the ledger ──────────────────────────────────────────────────────────────


def test_a_nested_runs_evidence_joins_the_callers_ledger():
    with evidence.collecting() as outer:
        evidence.add({"id": "X-1", "kind": "article", "source": "a"})
        with evidence.collecting() as inner:
            evidence.add({"id": "X-1", "kind": "article", "title": "제목", "source": "b"})
            evidence.add({"id": "X-2", "kind": "meeting", "source": "b"})
        assert set(inner.items) == {"X-1", "X-2"}
    assert outer.items["X-1"]["sources"] == ["a", "b"] and outer.items["X-1"]["title"] == "제목" and "X-2" in outer.items
    evidence.add({"id": "X-3", "kind": "article"})  # outside a ledger: nothing to add to, no error


# ── look-up-only handoffs ───────────────────────────────────────────────────


def _principal():
    return Principal(email="ceo@example.com", name="김민수", groups=("executive",), clearance=4)


def test_a_read_only_handoff_writes_nothing_even_under_an_a2a_run():
    seen = {}

    def runner(task):
        seen["viewer"] = current_viewer()
        return "조회 결과"

    tool = delegation.make_delegate_tool(name="ask_x", description="x", from_agent="advisory", to_agent="meeting",
                                         run=lambda: runner, read_only=True)
    with run_context(new_run(system="k_electronics", root_agent="k_super", origin="a2a", principal=_principal())):
        assert current_viewer().via_a2a  # the A2A run itself keeps its exceptions
        tool.invoke({"task": "회의 기준 조회"})
        assert current_viewer().via_a2a  # restored after the handoff
    v = seen["viewer"]
    assert v.can_write is False and v.via_a2a is False  # no write, and no A2A exception (reminder, task request)


def test_a_handoff_never_goes_back_up_the_chain():
    inner = delegation.make_delegate_tool(name="ask_advisory", description="x", from_agent="meeting", to_agent="advisory",
                                          run=lambda: (lambda task: "never"))
    outer = delegation.make_delegate_tool(name="ask_meeting", description="x", from_agent="advisory", to_agent="meeting",
                                          run=lambda: (lambda task: inner.invoke({"task": "되묻기"})))
    with run_context(new_run(system="k_electronics", root_agent="advisory")):
        answer, events = collect_events(lambda: outer.invoke({"task": "조회"}))
    assert answer == delegation.CYCLE
    assert [e for e in events if e["type"] == "delegation_skipped"][0]["reason"] == "cycle"


def test_a_handoff_tool_reports_the_delegation_not_a_tool_call(monkeypatch):
    tool = delegation.make_delegate_tool(name="ask_meeting_agent", description="x", from_agent="advisory", to_agent="meeting",
                                         run=lambda: (lambda task: "회의 결과"))
    from langchain_core.messages import AIMessage

    message = AIMessage(content="", tool_calls=[{"name": "ask_meeting_agent", "args": {"task": "조회"}, "id": "c1"}])
    with run_context(new_run(system="k_electronics", root_agent="advisory")):
        out, events = collect_events(lambda: agent_core.execute_tool_calls("advisory", message, {"ask_meeting_agent": tool}))
    assert [e["type"] for e in events] == ["delegation_start", "delegation_end"] and out[0].content == "회의 결과"
