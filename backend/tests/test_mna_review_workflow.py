"""run_mna_review workflow: persona-specific emphasis over the same data, context
signals, recommendation and conditions (no LLM needed)."""

import pytest

from functools import partial

from app.library.workflows.mna_review import PERSONAS, extract_context_signals, resolve_persona, review_acquisition as _review_acquisition
from app.systems.k_electronics import DATA_DIR
from app.library.workflows.mna_review import _profitability
from app.library.workflows.mna_review import _ip_risk
from app.library.workflows.mna_review import _business_value
from app.library.workflows.mna_review import _financial_downside
from app.library.workflows.mna_review import _deal_terms, _key_talent_retention
from app.library.workflows.mna_review import _integration

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
    "창업자 CTO 중심의 핵심 설계팀 retention 조건 확보",
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
    assert cto["conditions"][0] == "창업자 CTO 중심의 핵심 설계팀 retention 조건 확보"   
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

def test_profitability_positive_margin():
    """영업이익률이 양수이면 흑자로 평가한다."""
    company = {
        "financials": {
            "operating_margin": 0.061,
            "revenue_cagr_3y": 0.22,
        }
    }

    result = _profitability(company, {})

    assert result["verdict"] == "positive"
    assert result["level"] == "LOW"
    assert "흑자" in result["finding"]
    assert "6.1%" in result["finding"]


def test_profitability_negative_margin():
    """영업이익률이 음수이면 적자로 평가한다."""
    company = {
        "financials": {
            "operating_margin": -0.082,
            "revenue_cagr_3y": 0.31,
        }
    }

    result = _profitability(company, {})

    assert result["verdict"] == "concern"
    assert result["level"] == "MEDIUM"
    assert "적자" in result["finding"]
    assert "-8.2%" in result["finding"]

def test_ip_risk_without_dispute():
    """특허 분쟁이 없는 기업은 소송 위험으로 평가하지 않는다."""
    company = {
        "technology": {
            "ip_dispute": {
                "counterparty": "없음",
                "subject": "없음",
                "status": "진행 중인 분쟁 없음",
            }
        }
    }

    result = _ip_risk(company, {})

    assert result["verdict"] == "positive"
    assert result["level"] == "LOW"
    assert "분쟁 없음" in result["finding"]
    assert result["condition"] is None

def test_ip_risk_with_dispute():
    """특허 분쟁이 있는 기업은 소송 위험을 평가한다."""
    company = {
        "technology": {
            "ip_dispute": {
                "counterparty": "코어NPU",
                "subject": "NPU 메모리 구조 특허",
                "status": "소송 초기 단계, 결과 미확정",
            }
        }
    }

    result = _ip_risk(company, {})

    assert result["verdict"] == "concern"
    assert result["level"] == "MEDIUM"
    assert "코어NPU" in result["evidence"][0]
    assert result["condition"] == "코어NPU 특허소송 잠재손실 검증"

@pytest.mark.parametrize(
    "margin, expected_status",
    [
        (0.061, "흑자"),
        (0.0, "손익분기"),
        (-0.082, "적자"),
    ],
)
def test_business_value_profit_status(margin, expected_status):
    """사업 가치 평가가 실제 영업이익률에 맞는 문구를 사용한다."""
    company = {
        "financials": {
            "fiscal_year": 2025,
            "revenue_label": "9,800억 원",
            "revenue_cagr_3y": 0.22,
            "operating_margin": margin,
        }
    }

    result = _business_value(company, {})

    assert result["criterion"] == "business_value"
    assert result["verdict"] == "positive"
    assert f"영업이익률" in result["evidence"][2]
    assert f"({expected_status})" in result["evidence"][2]


def test_financial_downside_without_lawsuit_or_loss():
    """흑자이고 특허 분쟁이 없는 기업은 존재하지 않는 위험을 표시하지 않는다."""
    company = {
        "customers": {
            "largest": {
                "name": "주요 고객사",
                "revenue_share": 0.52,
            }
        },
        "technology": {
            "ip_dispute": {
                "counterparty": "없음",
                "subject": "없음",
                "status": "진행 중인 분쟁 없음",
            }
        },
        "financials": {
            "operating_margin": 0.061,
        },
    }

    result = _financial_downside(company, {})

    assert result["verdict"] == "concern"
    assert result["level"] == "MEDIUM"
    assert "주요 고객 이탈" in result["finding"]
    assert "특허소송" not in result["finding"]
    assert "적자 지속" not in result["finding"]
    assert result["condition"] is None


def test_financial_downside_with_lawsuit_and_loss():
    """고객 집중, 특허소송, 영업적자가 모두 있으면 각 위험을 반영한다."""
    company = {
        "customers": {
            "largest": {
                "name": "알파모바일",
                "revenue_share": 0.46,
            }
        },
        "technology": {
            "ip_dispute": {
                "counterparty": "코어NPU",
                "subject": "NPU 메모리 구조 특허",
                "status": "소송 초기 단계, 결과 미확정",
            }
        },
        "financials": {
            "operating_margin": -0.082,
        },
    }

    result = _financial_downside(company, {})

    assert result["verdict"] == "concern"
    assert result["level"] == "MEDIUM"
    assert "주요 고객 이탈" in result["finding"]
    assert "특허소송" in result["finding"]
    assert "적자 지속" in result["finding"]
    assert result["condition"] == "코어NPU 특허소송 잠재손실 검증"

    
@pytest.mark.parametrize(
    "core_team, fair_value",
    [
        ("창업자 CTO 중심의 핵심 설계팀", 2.3),
        ("공정 개발 총괄 중심의 핵심 공정팀", 1.5),
    ],
)
def test_company_specific_talent_and_deal_terms(core_team, fair_value):
    """기업별 핵심인력과 거래 조건이 일치하는지 확인한다."""
    company = {
        "valuation": {
            "fair_value_high_trillion_krw": fair_value,
        },
        "technology": {
            "core_team": core_team,
        },
    }

    deal = _deal_terms(company, {})
    talent = _key_talent_retention(company, {})

    expected_condition = f"{core_team} retention 조건 확보"

    assert deal["condition"] == expected_condition
    assert talent["condition"] == expected_condition
    assert core_team in deal["evidence"][1]
    assert core_team in talent["finding"]
    assert deal["verdict"] == "neutral"
    assert talent["verdict"] == "concern"

@pytest.mark.parametrize(
    "core_team",
    [
        "창업자 CTO 중심의 핵심 설계팀",
        "공정 개발 총괄 중심의 핵심 공정팀",
    ],
)
def test_integration_company_specific_team(core_team):
    """기업별 핵심인력이 기술 통합 평가에 반영되는지 확인한다."""
    company = {
        "technology": {
            "core_team": core_team,
            "post_acquisition_integration_months": [12, 18],
        }
    }

    result = _integration(company, {})

    assert result["criterion"] == "integration"
    assert result["verdict"] == "neutral"
    assert core_team in result["finding"]
    assert core_team in result["evidence"][1]
    assert "12~18개월" in result["finding"]