"""run_mna_review workflow: persona-specific emphasis over the same data, context
signals, recommendation and conditions (no LLM needed)."""

import pytest

from functools import partial

from app.library.workflows.mna_review import PERSONAS, extract_context_signals, resolve_persona, review_acquisition as _review_acquisition
from app.systems.k_electronics import DATA_DIR

# The workflow is system-neutral; these tests run it over K Electronics' company store.
review_acquisition = partial(_review_acquisition, path=DATA_DIR / "companies.json")

QUERY = "뉴로엣지 인수안의 본실사 진입 여부 검토"
CONTEXT = (
    "지난 전략회의 결정: CFO — 2.5조 원을 초과할 경우 가격 타당성 재검토, CTO — 핵심 설계팀 유지가 인수 성공의 핵심, "
    "CEO — 자체 개발 대비 최소 2년 이상 Time-to-Market 단축 필요. "
    "외부 리스크: 알파모바일(매출 46%)이 공급업체 다변화 검토, 코어NPU가 특허침해 소송 제기, 창업자 CTO 연구 자율성 중시 발언."
)
CANONICAL_CONDITIONS = {
    "인수 협상 기준가격 2.3조 원 이하",
    "창업자 CTO 및 핵심 설계팀 retention 조건 확보",
    "코어NPU 특허소송 잠재손실 검증",
    "알파모바일 계약 지속 여부 검증",
}


@pytest.mark.parametrize("persona", list(PERSONAS))
def test_same_data_same_recommendation_and_condition_set(persona):
    out = review_acquisition(QUERY, "뉴로엣지", persona, CONTEXT)
    assert out["found"] and out["persona"] == persona
    assert out["recommendation"] == "CONDITIONAL_PROCEED" and out["recommendation_label"] == "조건부 진행"
    assert set(out["conditions"]) == CANONICAL_CONDITIONS
    assert [a["criterion"] for a in out["assessments"]] == PERSONAS[persona]["criteria"]
    assert out["inputs"]["valuation"] == {"asking": "2.8조 원", "fair_value": "2.0~2.3조 원"}
    assert out["inputs"]["customers"]["largest_share"] == "46%"


def test_persona_changes_emphasis_and_condition_order():
    cfo = review_acquisition(QUERY, "뉴로엣지", "CFO", CONTEXT)
    cto = review_acquisition(QUERY, "뉴로엣지", "CTO", CONTEXT)
    ceo = review_acquisition(QUERY, "뉴로엣지", "CEO", CONTEXT)
    # CFO leads with price, CTO with talent, CEO with strategic fit.
    assert cfo["assessments"][0]["criterion"] == "valuation" and cfo["assessments"][0]["level"] == "HIGH"
    assert cto["assessments"][0]["criterion"] == "technology_fit"
    assert ceo["assessments"][0]["criterion"] == "strategic_fit"
    assert cfo["conditions"][0] == "인수 협상 기준가격 2.3조 원 이하"
    assert cto["conditions"][0] == "창업자 CTO 및 핵심 설계팀 retention 조건 확보"
    # Concerns surfaced to the persona differ.
    assert [c["label"] for c in cfo["concerns"]] == ["요구가격과 적정가치", "수익성", "고객 집중도", "재무적 downside"]
    assert [c["label"] for c in cto["concerns"]] == ["핵심 기술인력 유지", "IP / 특허 리스크"]
    assert ceo["positives"][:2] == ["AI NPU 설계 역량이 K전자 전략과 부합", "자체 개발 대비 약 24~30개월 빠른 시장 진입"]
    # Criteria outside the persona's list are still reported, condensed.
    assert {o["criterion"] for o in ceo["other_considerations"]} >= {"valuation", "key_talent_retention", "ip_risk"}


def test_context_signals_sharpen_evidence():
    signals = extract_context_signals(CONTEXT)
    assert signals == {
        "price_ceiling_trillion_krw": 2.5,
        "ttm_requirement_years": 2,
        "confirmed_risks": ["customer_concentration", "legal_dispute", "key_personnel"],
    }
    with_ctx = review_acquisition(QUERY, "뉴로엣지", "CFO", CONTEXT)
    without = review_acquisition(QUERY, "뉴로엣지", "CFO")
    valuation_with = next(a for a in with_ctx["assessments"] if a["criterion"] == "valuation")
    valuation_without = next(a for a in without["assessments"] if a["criterion"] == "valuation")
    assert any("2.5조 원" in e for e in valuation_with["evidence"])
    assert not any("2.5조 원" in e for e in valuation_without["evidence"])
    assert without["context_signals"] == {"price_ceiling_trillion_krw": None, "ttm_requirement_years": None, "confirmed_risks": []}
    # Verdicts do not depend on the context; only the evidence does.
    assert [(a["verdict"], a["level"]) for a in with_ctx["assessments"]] == [
        (a["verdict"], a["level"]) for a in without["assessments"]
    ]


def test_company_aliases_and_persona_aliases():
    for name in ("NeuroEdge", "뉴로엣지(NeuroEdge)", "neuroedge"):
        assert review_acquisition(QUERY, name, "CEO")["company"] == "뉴로엣지"
    assert resolve_persona("cfo") == "CFO" and resolve_persona("재무 관점") == "CFO" and resolve_persona("기술") == "CTO"
    assert resolve_persona("COO") is None


def test_unknown_company_and_persona_are_reported_not_raised():
    out = review_acquisition(QUERY, "존재하지않는회사", "CEO")
    assert out["found"] is False and "assessments" not in out
    out = review_acquisition(QUERY, "뉴로엣지", "COO")
    assert out["supported_personas"] == ["CEO", "CFO", "CTO"] and "error" in out
