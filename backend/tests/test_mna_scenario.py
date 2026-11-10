
"""M&A 인수가격 What-if 시나리오 테스트."""

import pytest

from app.library.workflows.mna_review import (
    find_company,
    _valuation,
    _profitability,
    _customer_concentration,
)

from app.library.workflows.mna_scenario import (
    apply_price_scenario,
    compare_price_scenarios,
    apply_profitability_scenario,
    apply_customer_scenario,
)

from app.systems.k_electronics import DATA_DIR
from app.library.workflows.mna_scenario import compare_mna_scenarios
from app.library.tools.run_mna_review import make_run_mna_review
from app.library.workflows.mna_scenario import compare_persona_scenarios



def test_price_scenario_preserves_original():
    """가격 변경 후에도 원본 기업 데이터는 유지되어야 한다."""

    company = find_company(
        "뉴로엣지",
        DATA_DIR / "companies.json",
    )
    assert company is not None

    scenario = apply_price_scenario(company, 2.2)

    assert company["valuation"]["asking_trillion_krw"] == 2.8
    assert scenario["valuation"]["asking_trillion_krw"] == 2.2
    assert scenario is not company

    original_result = _valuation(company, {})
    scenario_result = _valuation(scenario, {})

    assert original_result["verdict"] == "concern"
    assert original_result["level"] == "HIGH"

    assert scenario_result["verdict"] == "positive"


def test_price_scenario_rejects_invalid_values():
    """잘못된 인수가격은 적용하지 않아야 한다."""

    company = {
        "valuation": {
            "asking_trillion_krw": 2.8,
        }
    }

    for invalid_price in (0, -1, float("inf"), float("nan"), True, "2.2"):
        with pytest.raises(ValueError):
            apply_price_scenario(company, invalid_price)



def test_compare_price_scenarios():
    """인수가격 변경 전후의 평가 결과를 비교한다."""

    company = find_company(
        "뉴로엣지",
        DATA_DIR / "companies.json",
    )
    assert company is not None

    result = compare_price_scenarios(company, 2.2)

    assert result["company"] == "뉴로엣지"

    assert result["before"]["asking_price"] == 2.8
    assert result["after"]["asking_price"] == 2.2

    assert result["before"]["verdict"] == "concern"
    assert result["before"]["risk_level"] == "HIGH"

    assert result["after"]["verdict"] == "positive"
    assert result["after"]["risk_level"] is None

    assert result["changed"] is True

    # 비교 이후에도 원본 기업 데이터는 변경되지 않아야 한다.
    assert company["valuation"]["asking_trillion_krw"] == 2.8


def test_profitability_scenario():
    """적자에서 흑자로 바뀌면 수익성 평가도 변경되어야 한다."""

    company = find_company(
        "뉴로엣지",
        DATA_DIR / "companies.json",
    )
    assert company is not None

    scenario = apply_profitability_scenario(company, 0.05)

    before = _profitability(company, {})
    after = _profitability(scenario, {})

    assert company["financials"]["operating_margin"] == -0.082
    assert scenario["financials"]["operating_margin"] == 0.05

    assert before["verdict"] == "concern"
    assert after["verdict"] == "positive"
    assert "5%" in after["finding"]


def test_customer_scenario():
    """고객 의존도가 낮아지면 위험 수준이 변경되어야 한다."""

    company = find_company(
        "뉴로엣지",
        DATA_DIR / "companies.json",
    )
    assert company is not None

    scenario = apply_customer_scenario(company, 0.25)

    before = _customer_concentration(company, {})
    after = _customer_concentration(scenario, {})

    assert company["customers"]["largest"]["revenue_share"] == 0.46
    assert scenario["customers"]["largest"]["revenue_share"] == 0.25

    assert before["level"] == "HIGH"
    assert after["level"] == "MEDIUM"
    assert "25%" in after["finding"]


@pytest.mark.parametrize(
    "invalid_margin",
    [-1.5, 1.5, float("nan"), float("inf"), True, "0.05"],
)
def test_profitability_scenario_invalid_values(invalid_margin):
    """잘못된 영업이익률은 거부해야 한다."""

    company = {"financials": {"operating_margin": -0.082}}

    with pytest.raises(ValueError):
        apply_profitability_scenario(company, invalid_margin)


@pytest.mark.parametrize(
    "invalid_share",
    [-0.1, 1.1, float("nan"), float("inf"), True, "0.25"],
)
def test_customer_scenario_invalid_values(invalid_share):
    """잘못된 고객 매출 비중은 거부해야 한다."""

    company = {
        "customers": {
            "largest": {"revenue_share": 0.46}
        }
    }

    with pytest.raises(ValueError):
        apply_customer_scenario(company, invalid_share)



def test_compare_mna_scenarios_all_conditions():
    """가격, 수익성, 고객 의존도를 동시에 변경해 비교한다."""

    company = find_company(
        "뉴로엣지",
        DATA_DIR / "companies.json",
    )
    assert company is not None

    result = compare_mna_scenarios(
        company,
        asking_trillion_krw=2.2,
        operating_margin=0.05,
        revenue_share=0.25,
    )

    assert result["company"] == "뉴로엣지"

    # 기존 데이터가 변경되지 않았는지 확인
    assert company["valuation"]["asking_trillion_krw"] == 2.8
    assert company["financials"]["operating_margin"] == -0.082
    assert company["customers"]["largest"]["revenue_share"] == 0.46

    # 변경 조건 확인
    assert result["assumptions"] == {
        "asking_trillion_krw": 2.2,
        "operating_margin": 0.05,
        "revenue_share": 0.25,
    }

    # 기존 규칙상 핵심인력 위험이 남아 조건부 진행 유지
    assert result["before_recommendation"] == "CONDITIONAL_PROCEED"
    assert result["after_recommendation"] == "CONDITIONAL_PROCEED"

    changes = {
        item["criterion"]: item
        for item in result["changes"]
    }

    assert "valuation" in changes
    assert "profitability" in changes
    assert "customer_concentration" in changes

    assert changes["valuation"]["before"]["level"] == "HIGH"
    assert changes["valuation"]["after"]["verdict"] == "positive"

    assert changes["profitability"]["before"]["verdict"] == "concern"
    assert changes["profitability"]["after"]["verdict"] == "positive"

    assert changes["customer_concentration"]["before"]["level"] == "HIGH"
    assert changes["customer_concentration"]["after"]["level"] == "MEDIUM"


def test_compare_mna_scenarios_single_condition():
    """인수가격 하나만 변경해도 비교가 가능해야 한다."""

    company = find_company(
        "뉴로엣지",
        DATA_DIR / "companies.json",
    )
    assert company is not None

    result = compare_mna_scenarios(
        company,
        asking_trillion_krw=2.2,
    )

    assert result["assumptions"]["asking_trillion_krw"] == 2.2
    assert result["assumptions"]["operating_margin"] is None
    assert result["assumptions"]["revenue_share"] is None

    changed_criteria = {
        item["criterion"]
        for item in result["changes"]
    }

    assert "valuation" in changed_criteria
    assert "profitability" not in changed_criteria


def test_compare_mna_scenarios_requires_change():
    """변경 조건이 없으면 오류를 반환해야 한다."""

    company = find_company(
        "뉴로엣지",
        DATA_DIR / "companies.json",
    )
    assert company is not None

    with pytest.raises(ValueError):
        compare_mna_scenarios(company)


def test_mna_tool_existing_review():
    """조건 변경이 없으면 기존 평가를 실행한다."""

    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "뉴로엣지 인수 검토",
        "company": "뉴로엣지",
        "persona": "CFO",
    })

    assert result["found"] is True
    assert result["persona"] == "CFO"
    assert "recommendation" in result


def test_mna_tool_what_if_price():
    """사용자가 변경한 인수가격을 평가한다."""

    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "뉴로엣지를 2.2조 원에 인수하면?",
        "company": "뉴로엣지",
        "persona": "CFO",
        "asking_trillion_krw": 2.2,
    })

    assert result["found"] is True
    assert result["mode"] == "what_if"
    assert result["assumptions"]["asking_trillion_krw"] == 2.2
    assert result["before_recommendation"] == "CONDITIONAL_PROCEED"
    assert result["after_recommendation"] == "CONDITIONAL_PROCEED"

    changed = {
        item["criterion"]: item
        for item in result["changes"]
    }

    assert "valuation" in changed
    assert changed["valuation"]["after"]["verdict"] == "positive"


def test_mna_tool_what_if_all_conditions():
    """세 가지 조건을 동시에 변경한다."""

    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "인수가격과 수익성 및 고객 의존도를 개선하면?",
        "company": "뉴로엣지",
        "persona": "CFO",
        "asking_trillion_krw": 2.2,
        "operating_margin": 0.05,
        "revenue_share": 0.25,
    })

    assert result["mode"] == "what_if"

    changed = {
        item["criterion"]
        for item in result["changes"]
    }

    assert "valuation" in changed
    assert "profitability" in changed
    assert "customer_concentration" in changed

def test_compare_persona_scenarios_cfo():
    company = find_company("뉴로엣지", DATA_DIR / "companies.json")
    assert company is not None

    result = compare_persona_scenarios(
        company,
        persona="CFO",
        asking_trillion_krw=2.2,
        operating_margin=0.05,
        revenue_share=0.25,
    )

    assert result["persona"] == "CFO"
    assert result["persona_label"] == "CFO 관점"
    assert result["changes"][0]["criterion"] == "valuation"


def test_compare_persona_scenarios_invalid_persona():
    company = find_company("뉴로엣지", DATA_DIR / "companies.json")
    assert company is not None

    with pytest.raises(ValueError):
        compare_persona_scenarios(
            company,
            persona="INVALID",
            asking_trillion_krw=2.2,
        )


def test_mna_tool_what_if_persona_cfo():
    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "뉴로엣지를 2.2조 원에 인수하면?",
        "company": "뉴로엣지",
        "persona": "CFO",
        "asking_trillion_krw": 2.2,
        "operating_margin": 0.05,
        "revenue_share": 0.25,
    })

    assert result["mode"] == "what_if"
    assert result["persona"] == "CFO"
    assert result["persona_label"] == "CFO 관점"
    assert result["changes"][0]["criterion"] == "valuation"



def test_mna_scenario_conditions_comparison():
    """가격 변경 후 권고 조건의 변화를 확인한다."""

    company = find_company(
        "뉴로엣지",
        DATA_DIR / "companies.json",
    )
    assert company is not None

    result = compare_mna_scenarios(
        company,
        asking_trillion_krw=2.2,
    )

    assert "before_conditions" in result
    assert "after_conditions" in result
    assert "removed_conditions" in result
    assert "added_conditions" in result

    # 가격 관련 조건은 변경 후 제거될 수 있다.
    assert any(
        "인수 협상 기준가격" in condition
        for condition in result["removed_conditions"]
    )

    # 핵심인력 유지 조건은 여전히 남아 있어야 한다.
    assert any(
        "retention" in condition
        for condition in result["after_conditions"]
    )


def test_mna_tool_returns_conditions():
    """Agent Tool이 변경 전후 권고 조건을 반환하는지 확인한다."""

    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "뉴로엣지를 2.2조 원에 인수하면?",
        "company": "뉴로엣지",
        "persona": "CFO",
        "asking_trillion_krw": 2.2,
    })

    assert result["mode"] == "what_if"
    assert "before_conditions" in result
    assert "after_conditions" in result
    assert "removed_conditions" in result
    assert "added_conditions" in result



def test_whatif_tool_ui_compatibility():
    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "뉴로엣지를 2.2조 원에 인수하면?",
        "company": "뉴로엣지",
        "persona": "CFO",
        "asking_trillion_krw": 2.2,
    })

    assert result["found"] is True
    assert result["mode"] == "what_if"

    # 기존 UI에서 요구하는 데이터
    assert result["inputs"]["valuation"]["asking"] == "2.2조 원"
    assert isinstance(result["assessments"], list)
    assert result["recommendation"] == result["after_recommendation"]
    assert result["recommendation_label"]
    assert result["conditions"] == result["after_conditions"]

    # 새로운 비교 데이터도 유지되는지 확인
    assert "changes" in result
    assert "before_conditions" in result
    assert "after_conditions" in result
    assert "removed_conditions" in result
    assert "added_conditions" in result


def test_what_if_respects_meeting_context():
    """What-if 평가에서도 회의에서 정한 가격 상한을 반영한다."""

    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "뉴로엣지 인수가격을 2.7조 원으로 변경하면?",
        "company": "뉴로엣지",
        "persona": "CFO",
        "asking_trillion_krw": 2.7,
        "context": (
            "회의에서 인수가격 상한은 2.5조 원 이하로 확정했다."
        ),
    })

    assert result["found"] is True
    assert result["mode"] == "what_if"

    # CFO 평가 항목 중 인수가격 평가 확인
    valuation = next(
        assessment
        for assessment in result["assessments"]
        if assessment["criterion"] == "valuation"
    )

    # 변경된 인수가격이 회의 기준을 초과한다는
    # 사실이 평가 근거에 포함되어야 한다.
    evidence = " ".join(valuation["evidence"])

    assert "2.5조 원" in evidence
    assert "요청 맥락의 내부 기준" in evidence
    assert "초과" in evidence

    # 인수가격의 변경값도 유지해야 한다.
    assert result["inputs"]["valuation"]["asking"] == "2.7조 원"



def test_what_if_respects_customer_and_patent_context():
    """회의에서 확인된 고객 및 특허 위험을 What-if에 반영한다."""

    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "뉴로엣지 인수가격을 2.1조 원으로 변경하면?",
        "company": "뉴로엣지",
        "persona": "CFO",
        "asking_trillion_krw": 2.1,
        "context": (
            "회의에서 알파모바일 고객 집중 위험과 "
            "코어NPU 특허소송 위험이 확인되었다."
        ),
    })

    assert result["found"] is True
    assert result["mode"] == "what_if"

    assessments = {
        item["criterion"]: item
        for item in result["assessments"]
    }

    # 1. 고객 집중 위험에 회의 맥락이 반영되는지 확인
    customer = assessments["customer_concentration"]

    customer_evidence = " ".join(customer["evidence"])

    assert "46%" in customer_evidence
    assert "요청 맥락" in customer_evidence
    assert "외부 리스크가 확인됨" in customer_evidence

    # 2. 특허소송 위험에 회의 맥락이 반영되는지 확인
    # CFO의 기본 평가 우선순위에는 ip_risk가 없으므로,
    # 실제 Tool의 변경 전후 평가 결과와는 별도로
    # CTO 관점에서도 동일한 맥락을 확인한다.
    cto_result = tool.invoke({
        "query": "뉴로엣지 인수가격을 2.1조 원으로 변경하면?",
        "company": "뉴로엣지",
        "persona": "CTO",
        "asking_trillion_krw": 2.1,
        "context": (
            "회의에서 알파모바일 고객 집중 위험과 "
            "코어NPU 특허소송 위험이 확인되었다."
        ),
    })

    assert cto_result["found"] is True
    assert cto_result["mode"] == "what_if"

    cto_assessments = {
        item["criterion"]: item
        for item in cto_result["assessments"]
    }

    patent = cto_assessments["ip_risk"]

    patent_evidence = " ".join(patent["evidence"])

    assert "코어NPU" in patent_evidence
    assert "요청 맥락에서 소송 리스크가 확인됨" in patent_evidence

    # 3. 회의 맥락을 추가해도 What-if 변경 조건은 유지된다.
    assert result["inputs"]["valuation"]["asking"] == "2.1조 원"
    assert cto_result["inputs"]["valuation"]["asking"] == "2.1조 원"

    # 4. 최종 권고도 기존 Workflow 결과와 일치해야 한다.
    assert result["recommendation"] == result["after_recommendation"]
    assert cto_result["recommendation"] == cto_result["after_recommendation"]
