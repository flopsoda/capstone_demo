
"""CEO·CFO·CTO 통합 M&A 평가 테스트."""

from app.library.tools.run_mna_review import make_run_mna_review
from app.systems.k_electronics import DATA_DIR


def test_executive_summary_contains_all_personas():
    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "인수 타당성을 종합적으로 검토해줘",
        "company": "뉴로엣지",
        "persona": "ALL",
    })

    assert result["found"] is True
    assert result["persona"] == "ALL"
    assert result["mode"] == "executive_summary"

    assert set(result["executive_reviews"]) == {
        "CEO", "CFO", "CTO"
    }

    for persona in ("CEO", "CFO", "CTO"):
        review = result["executive_reviews"][persona]
        assert review["assessments"]
        assert review["persona_focus"]


def test_executive_summary_matches_individual_recommendations():
    tool = make_run_mna_review(DATA_DIR / "companies.json")

    summary = tool.invoke({
        "query": "인수 검토",
        "company": "뉴로엣지",
        "persona": "ALL",
    })

    for persona in ("CEO", "CFO", "CTO"):
        individual = tool.invoke({
            "query": "인수 검토",
            "company": "뉴로엣지",
            "persona": persona,
        })

        assert summary["recommendation"] == individual["recommendation"]


def test_executive_summary_unknown_company():
    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "인수 검토",
        "company": "존재하지않는기업123",
        "persona": "ALL",
    })

    assert result["found"] is False


def test_executive_summary_key_risks():
    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "M&A executive review",
        "company": "\ub274\ub85c\uc5e3\uc9c0",
        "persona": "ALL",
    })

    assert result["found"] is True

    risks = result["key_risks"]

    assert risks
    assert all(risk["criterion"] for risk in risks)
    assert all(risk["personas"] for risk in risks)

    criteria = [risk["criterion"] for risk in risks]
    assert len(criteria) == len(set(criteria))

    assert "valuation" in criteria
    assert "profitability" in criteria
    assert "customer_concentration" in criteria
    assert "ip_risk" in criteria


def test_executive_summary_risk_priority():
    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "M&A executive review",
        "company": "\ub274\ub85c\uc5e3\uc9c0",
        "persona": "ALL",
    })

    risks = result["key_risks"]

    priority = {
        "HIGH": 0,
        "MEDIUM": 1,
        "LOW": 2,
    }

    levels = [
        priority.get(risk["level"], 3)
        for risk in risks
    ]

    assert levels == sorted(levels)

    criteria = [risk["criterion"] for risk in risks]
    assert len(criteria) == len(set(criteria))

    for risk in risks:
        assert risk["personas"]
        assert risk["label"]
        assert risk["finding"]


def test_executive_summary_decision_rationale():
    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "M&A executive review",
        "company": "\ub274\ub85c\uc5e3\uc9c0",
        "persona": "ALL",
    })

    rationale = result["decision_rationale"]

    # 최종 권고안 일치 여부
    assert rationale["recommendation"] == result["recommendation"]

    # 인수 조건 일치 여부
    assert rationale["required_conditions"] == result["conditions"]

    # HIGH 위험 항목 일치 여부
    expected_high_risks = [
        risk["label"]
        for risk in result["key_risks"]
        if risk["level"] == "HIGH"
    ]

    assert rationale["high_risks"] == expected_high_risks

    # 요약문 생성 여부
    assert rationale["summary"]
    assert result["recommendation"] in rationale["summary"]


def test_executive_summary_key_opportunities():
    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "M&A executive review",
        "company": "\ub274\ub85c\uc5e3\uc9c0",
        "persona": "ALL",
    })

    assert result["found"] is True

    opportunities = result["key_opportunities"]
    assert opportunities

    # 중복 항목이 없는지 확인
    criteria = [item["criterion"] for item in opportunities]
    assert len(criteria) == len(set(criteria))

    # 긍정 평가만 포함되어 있는지 확인
    positive_criteria = {
        item["criterion"]
        for review in result["executive_reviews"].values()
        for item in review["assessments"]
        if item["verdict"] == "positive"
    }

    assert set(criteria) == positive_criteria

    # 필수 정보 확인
    for item in opportunities:
        assert item["label"]
        assert item["finding"]
        assert item["personas"]

    # 여러 경영진이 긍정 평가한 항목의 담당 관점 확인
    for item in opportunities:
        expected_personas = {
            persona
            for persona, review in result["executive_reviews"].items()
            if any(
                assessment["criterion"] == item["criterion"]
                and assessment["verdict"] == "positive"
                for assessment in review["assessments"]
            )
        }

        assert set(item["personas"]) == expected_personas


def test_executive_summary_decision_positive_factors():
    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "M&A executive review",
        "company": "\ub274\ub85c\uc5e3\uc9c0",
        "persona": "ALL",
    })

    assert result["found"] is True

    rationale = result["decision_rationale"]

    expected_positive_factors = [
        item["label"]
        for item in result["key_opportunities"]
    ]

    # 긍정 요인이 기존 평가 결과와 일치하는지 확인
    assert rationale["positive_factors"] == expected_positive_factors

    # 요약문에 긍정 요인 건수가 반영되었는지 확인
    # 긍정 요인이 요약문에 실제로 포함되는지 확인
    for factor in expected_positive_factors:
        assert factor in rationale["summary"]

    # HIGH 위험 항목도 요약문에 포함되는지 확인
    for risk in rationale["high_risks"]:
        assert risk in rationale["summary"]

    # 최종 권고안과 인수 조건 건수 확인
    assert result["recommendation"] in rationale["summary"]
    assert f"검토할 조건은 {len(result['conditions'])}건" in rationale["summary"]
    

def test_executive_summary_conditions_in_report():
    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "M&A executive review",
        "company": "\ub274\ub85c\uc5e3\uc9c0",
        "persona": "ALL",
    })

    assert result["found"] is True

    rationale = result["decision_rationale"]
    conditions = result["conditions"]
    summary = rationale["summary"]

    # 인수 조건 목록 일치 여부
    assert rationale["required_conditions"] == conditions

    # 모든 인수 조건이 보고서에 포함되는지 확인
    for condition in conditions:
        assert condition in summary

    # 조건 개수가 정확한지 확인
    assert f"검토할 조건은 {len(conditions)}건" in summary


from app.library.workflows.mna_executive_summary import (
    build_decision_rationale,
)


def test_decision_rationale_without_risks_or_conditions():
    result = build_decision_rationale(
        recommendation="PROCEED",
        key_risks=[],
        conditions=[],
        key_opportunities=[],
    )

    assert result["recommendation"] == "PROCEED"
    assert result["positive_factors"] == []
    assert result["high_risks"] == []
    assert result["required_conditions"] == []

    assert "확인된 긍정 요인 없음" in result["summary"]
    assert "HIGH 등급 위험 없음" in result["summary"]
    assert "추가 인수 조건 없음" in result["summary"]


def test_key_risks_multiple_personas():
    from app.library.workflows.mna_executive_summary import (
        extract_key_risks,
    )

    reviews = {
        "CEO": {
            "assessments": [
                {
                    "criterion": "valuation",
                    "label": "기업가치",
                    "verdict": "concern",
                    "level": "HIGH",
                    "finding": "인수가격이 높음",
                }
            ]
        },
        "CFO": {
            "assessments": [
                {
                    "criterion": "valuation",
                    "label": "기업가치",
                    "verdict": "concern",
                    "level": "HIGH",
                    "finding": "인수가격이 높음",
                }
            ]
        },
    }

    risks = extract_key_risks(reviews)

    assert len(risks) == 1
    assert risks[0]["criterion"] == "valuation"
    assert risks[0]["level"] == "HIGH"
    assert set(risks[0]["personas"]) == {"CEO", "CFO"}


def test_key_risks_different_levels():
    from app.library.workflows.mna_executive_summary import (
        extract_key_risks,
    )

    reviews = {
        "CEO": {
            "assessments": [
                {
                    "criterion": "valuation",
                    "label": "기업가치",
                    "verdict": "concern",
                    "level": "MEDIUM",
                    "finding": "가격 검토 필요",
                }
            ]
        },
        "CFO": {
            "assessments": [
                {
                    "criterion": "valuation",
                    "label": "기업가치",
                    "verdict": "concern",
                    "level": "HIGH",
                    "finding": "인수가격 과다",
                }
            ]
        },
    }

    risks = extract_key_risks(reviews)

    assert len(risks) == 1
    assert risks[0]["level"] == "HIGH"
    assert risks[0]["finding"] == "인수가격 과다"
    assert set(risks[0]["personas"]) == {"CEO", "CFO"}



def test_verified_report_includes_three_executive_views():
    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "뉴로엣지 M&A 종합 보고서",
        "company": "뉴로엣지",
        "persona": "ALL",
    })

    report = result["verified_report"]

    for persona in ("CEO", "CFO", "CTO"):
        assert f"- {persona} 관점" in report

        first_assessment = result[
            "executive_reviews"
        ][persona]["assessments"][0]

        assert first_assessment["finding"] in report


def test_invalid_price_returns_tool_error():
    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "인수가격이 0조 원이라면?",
        "company": "뉴로엣지",
        "persona": "CFO",
        "asking_trillion_krw": 0,
    })

    assert result["error_type"] == "INVALID_SCENARIO"
    assert "인수가격" in result["error"]
