
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



def test_advisory_retries_on_validation_failure(monkeypatch):
    from langchain_core.messages import AIMessage
    from app.runtime.events import collect_events, emit
    from app.systems.k_electronics.agents import advisory

    # 1. 실제 M&A Workflow 결과 준비
    tool = make_run_mna_review(DATA_DIR / "companies.json")

    tool_result = tool.invoke({
        "query": "뉴로엣지 경영진 종합 평가",
        "company": "뉴로엣지",
        "persona": "ALL",
    })

    assert tool_result["found"] is True
    assert tool_result["mode"] == "executive_summary"

    inputs = tool_result["inputs"]

    # 2. 재작성에 성공한 정상 보고서 준비
    valid_report = "\n".join([
        f"인수 대상 기업: {inputs['company']}",
        f"연매출: {inputs['financials']['revenue']}",
        f"영업이익률: {inputs['financials']['operating_margin']}",
        f"인수가격: {inputs['valuation']['asking']}",
        f"최대 고객: {inputs['customers']['largest']}",
        f"고객 매출 비중: {inputs['customers']['largest_share']}",
        f"특허 분쟁: {inputs['technology']['ip_dispute']}",
        "CEO 관점: 전략적 가치 검토",
        "CFO 관점: 재무적 위험 검토",
        "CTO 관점: 기술적 가치 검토",
        f"최종 권고: {tool_result['recommendation_label']}",
        "인수 조건:",
        *tool_result["conditions"],
        "종합 의견",
        "- 전략적 측면에서 뉴로엣지의 AI NPU 설계 기술 확보와 "
        "시장 진입 기간 단축은 K전자에 의미 있는 성장 기회다.",
        "- 다만 높은 인수가격, 알파모바일 고객 집중도, "
        "핵심 설계인력 이탈과 특허소송 위험을 고려하면 "
        "즉시 인수를 확정하기 어렵다.",
        "- 따라서 협상가격 2.3조 원 이하, 주요 고객 계약 유지 검증, "
        "특허소송 잠재손실 검증, 핵심 인력 유지 조건을 "
        "충족하는 경우에 한해 조건부 진행을 권고한다.",
    ])

    # 정상 보고서가 실제 검증 기준을 충족하는지 확인
    assert advisory.validate_mna_report(
        valid_report, tool_result
    )["valid"] is True

    # 3. 실행 횟수 확인
    workflow_call_count = 0
    rewrite_call_count = 0

    # 4. 최초 Specialist 실행을 모의 처리
    def fake_run_specialist(*args, **kwargs):
        nonlocal workflow_call_count
        workflow_call_count += 1

        emit({
            "type": "agent_start",
            "agent": "advisory",
            "request": "뉴로엣지 종합 보고서",
        })

        emit({
            "type": "tool_start",
            "agent": "advisory",
            "tool": "run_mna_review",
            "args": {
                "company": "뉴로엣지",
                "persona": "ALL",
            },
        })

        emit({
            "type": "tool_end",
            "agent": "advisory",
            "tool": "run_mna_review",
            "result": tool_result,
        })

        emit({
            "type": "agent_token",
            "agent": "advisory",
            "text": "검증 실패 답변",
        })

        emit({
            "type": "agent_end",
            "agent": "advisory",
            "response": "검증 실패 답변",
        })

        return "검증 실패 답변"

    # 5. 보고서 재작성용 LLM을 모의 처리
    class FakeModel:
        def invoke(self, messages):
            nonlocal rewrite_call_count
            rewrite_call_count += 1

            # 기존 Tool 결과가 재작성 요청에 포함되는지 확인
            prompt = "\n".join(
                str(message.content) for message in messages
            )

            assert "뉴로엣지" in prompt
            assert "run_mna_review" in prompt
            assert tool_result["recommendation"] in prompt

            return AIMessage(content=valid_report)

    # 6. 경영자문 Agent에서 사용하는 함수만 교체
    monkeypatch.setattr(
        advisory,
        "run_specialist",
        fake_run_specialist,
    )

    monkeypatch.setattr(
        advisory,
        "build_advisory_agent",
        lambda: None,
    )

    monkeypatch.setattr(
        advisory,
        "get_chat_model",
        lambda: FakeModel(),
    )

    # 7. 경영자문 Agent 실행
    response, events = collect_events(
        lambda: advisory.run_advisory_agent(
            "뉴로엣지 종합 보고서",
            invocation="direct",
        )
    )

    # 8. Workflow는 한 번만 실행되어야 한다.
    assert workflow_call_count == 1

    # 9. 보고서 재작성 LLM도 한 번만 호출되어야 한다.
    assert rewrite_call_count == 1

    # 10. 최종 응답은 재작성된 보고서여야 한다.
    assert response == valid_report
    assert "검증 실패 답변" not in response

    # 11. 원본 Tool 결과 기준으로 사실 검증
    validation = advisory.validate_mna_report(
        response,
        tool_result,
    )

    assert validation["valid"] is True
    assert validation["missing"] == []

    # 12. 실제 Tool 호출 기록은 한 번만 존재해야 한다.
    tool_events = [
        event for event in events
        if event.get("type") == "tool_end"
        and event.get("tool") == "run_mna_review"
    ]

    assert len(tool_events) == 1
    assert tool_events[0]["result"] == tool_result

    # 13. 검증 실패한 보고서가 이벤트로 전달되지 않아야 한다.
    token_events = [
        event["text"]
        for event in events
        if event.get("type") == "agent_token"
    ]

    assert token_events == [valid_report]

    # 14. 최종 이벤트도 검증된 보고서여야 한다.
    assert events[0]["type"] == "agent_start"
    assert events[-1]["type"] == "agent_end"
    assert events[-1]["response"] == valid_report


def test_executive_report_rejects_missing_final_rationale():
    from app.systems.k_electronics.agents.advisory import (
        validate_mna_report,
    )

    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "뉴로엣지 종합 평가",
        "company": "뉴로엣지",
        "persona": "ALL",
    })

    # 필수 데이터는 있지만 종합 의견이 없는 보고서
    report = result["verified_report"]

    validation = validate_mna_report(report, result)

    assert validation["valid"] is False
    assert "FINAL_RATIONALE" in validation["missing"]

    
def test_individual_mna_report_validation():
    from app.systems.k_electronics.agents.advisory import (
        validate_individual_mna_report,
    )

    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "뉴로엣지 인수를 CFO 관점에서 평가해줘",
        "company": "뉴로엣지",
        "persona": "CFO",
    })

    assert result["found"] is True

    inputs = result["inputs"]

    # 정상 보고서 구성
    valid_report = "\n".join([
        "CFO 관점 M&A 평가",
        f"기업명: {inputs['company']}",
        f"매출액: {inputs['financials']['revenue']}",
        f"영업이익률: {inputs['financials']['operating_margin']}",
        f"인수가격: {inputs['valuation']['asking']}",
        f"최대 고객: {inputs['customers']['largest']}",
        f"고객 비중: {inputs['customers']['largest_share']}",
        f"최종 권고: {result['recommendation_label']}",
        "인수 조건:",
        *result["conditions"],
    ])

    # 정상 보고서는 검증 통과
    validation = validate_individual_mna_report(
        valid_report, result
    )

    assert validation["valid"] is True
    assert validation["missing"] == []

    # 인수 조건을 삭제하면 검증 실패
    invalid_report = valid_report.replace(
        result["conditions"][0], ""
    )

    validation = validate_individual_mna_report(
        invalid_report, result
    )

    assert validation["valid"] is False
    assert "CONDITION_1" in validation["missing"]



def test_what_if_report_validation():
    from app.systems.k_electronics.agents.advisory import (
        validate_what_if_report,
    )

    # 1. 실제 M&A Workflow 결과 준비
    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "뉴로엣지 인수가격을 2.1조 원으로 변경하면?",
        "company": "뉴로엣지",
        "persona": "CFO",
        "asking_trillion_krw": 2.1,
    })

    assert result["found"] is True
    assert result["mode"] == "what_if"
    assert result["persona"] == "CFO"

    before = result["before_inputs"]
    after = result["after_inputs"]

    # 2. 올바른 What-if 보고서 작성
    valid_report = "\n".join([
        f"기업명: {result['company']}",
        f"평가 관점: {result['persona']}",
        "변경 조건: 인수가격 인하",
        f"변경 전 인수가격: {before['valuation']['asking']}",
        f"변경 후 인수가격: {after['valuation']['asking']}",
        f"변경 전 권고: {result['before_recommendation']}",
        f"변경 후 권고: {result['after_recommendation']}",
        "변경 후 인수 조건:",
        *result["after_conditions"],
        "제거된 인수 조건:",
        *result["removed_conditions"],
        "추가된 인수 조건:",
        *result["added_conditions"],
    ])

    # 3. 정상 보고서 검증
    validation = validate_what_if_report(
        valid_report,
        result,
    )

    assert validation["valid"] is True
    assert validation["missing"] == []

    # 4. 인수 조건 누락 시 검증 실패
    invalid_conditions_report = valid_report.replace(
        result["after_conditions"][0],
        "",
    )

    validation = validate_what_if_report(
        invalid_conditions_report,
        result,
    )

    assert validation["valid"] is False
    assert "AFTER_CONDITION_1" in validation["missing"]

    # 5. 변경 후 인수가격 누락 시 검증 실패
    invalid_price_report = valid_report.replace(
        f"변경 후 인수가격: {after['valuation']['asking']}",
        "변경 후 인수가격: 확인 필요",
    )

    validation = validate_what_if_report(
        invalid_price_report,
        result,
    )

    assert validation["valid"] is False
    assert "AFTER_PRICE" in validation["missing"]



def test_advisory_what_if_retries_on_validation_failure(monkeypatch):
    from langchain_core.messages import AIMessage
    from app.runtime.events import collect_events, emit
    from app.systems.k_electronics.agents import advisory

    # 1. 실제 What-if Workflow 결과 준비
    tool = make_run_mna_review(DATA_DIR / "companies.json")

    tool_result = tool.invoke({
        "query": "뉴로엣지 인수가격을 2.1조 원으로 변경",
        "company": "뉴로엣지",
        "persona": "CFO",
        "asking_trillion_krw": 2.1,
    })

    assert tool_result["found"] is True
    assert tool_result["mode"] == "what_if"

    before = tool_result["before_inputs"]
    after = tool_result["after_inputs"]

    # 2. 검증에 통과할 정상 보고서
    valid_report = "\n".join([
        f"기업명: {tool_result['company']}",
        f"평가 관점: {tool_result['persona']}",
        "변경 조건: 인수가격 인하",
        f"변경 전 인수가격: {before['valuation']['asking']}",
        f"변경 후 인수가격: {after['valuation']['asking']}",
        f"변경 전 권고: {tool_result['before_recommendation']}",
        f"변경 후 권고: {tool_result['after_recommendation']}",
        "변경 후 인수 조건:",
        *tool_result["after_conditions"],
        "제거된 인수 조건:",
        *tool_result["removed_conditions"],
        "추가된 인수 조건:",
        *tool_result["added_conditions"],
    ])

    assert advisory.validate_what_if_report(
        valid_report, tool_result
    )["valid"] is True

    # 3. 실행 횟수 기록
    workflow_call_count = 0
    rewrite_call_count = 0

    # 4. 최초 Workflow 실행 결과를 모의 처리
    def fake_run_specialist(*args, **kwargs):
        nonlocal workflow_call_count
        workflow_call_count += 1

        emit({
            "type": "agent_start",
            "agent": "advisory",
            "request": "뉴로엣지 What-if 분석",
        })

        emit({
            "type": "tool_start",
            "agent": "advisory",
            "tool": "run_mna_review",
            "args": {
                "company": "뉴로엣지",
                "persona": "CFO",
                "asking_trillion_krw": 2.1,
            },
        })

        emit({
            "type": "tool_end",
            "agent": "advisory",
            "tool": "run_mna_review",
            "result": tool_result,
        })

        emit({
            "type": "agent_token",
            "agent": "advisory",
            "text": "검증 실패 보고서",
        })

        emit({
            "type": "agent_end",
            "agent": "advisory",
            "response": "검증 실패 보고서",
        })

        return "검증 실패 보고서"

    # 5. 재작성 LLM을 모의 처리
    class FakeModel:
        def invoke(self, messages):
            nonlocal rewrite_call_count
            rewrite_call_count += 1

            prompt = "\n".join(
                str(message.content)
                for message in messages
            )

            # 원래 기업과 변경 조건이 보존됐는지 확인
            assert "뉴로엣지" in prompt
            assert "CFO" in prompt
            assert "2.1" in prompt
            assert tool_result["after_recommendation"] in prompt

            # 최초 Tool의 인수 조건도 유지되어야 한다.
            for condition in tool_result["after_conditions"]:
                assert condition in prompt

            return AIMessage(content=valid_report)

    # 6. 경영자문 Agent에서 사용하는 함수만 교체
    monkeypatch.setattr(
        advisory,
        "run_specialist",
        fake_run_specialist,
    )

    monkeypatch.setattr(
        advisory,
        "build_advisory_agent",
        lambda: None,
    )

    monkeypatch.setattr(
        advisory,
        "get_chat_model",
        lambda: FakeModel(),
    )

    # 7. Agent 실행
    response, events = collect_events(
        lambda: advisory.run_advisory_agent(
            "뉴로엣지 인수가격을 2.1조 원으로 변경하면 "
            "CFO 관점에서 어떻게 달라지는지 비교해줘.",
            invocation="direct",
        )
    )

    # 8. Workflow는 한 번만 실행되어야 한다.
    assert workflow_call_count == 1

    # 9. LLM 재작성은 한 번만 실행되어야 한다.
    assert rewrite_call_count == 1

    # 10. 검증된 최종 보고서가 반환되어야 한다.
    assert response == valid_report

    validation = advisory.validate_what_if_report(
        response,
        tool_result,
    )

    assert validation["valid"] is True
    assert validation["missing"] == []

    # 11. Tool 호출 이벤트는 한 번만 존재해야 한다.
    tool_events = [
        event for event in events
        if event.get("type") == "tool_end"
        and event.get("tool") == "run_mna_review"
    ]

    assert len(tool_events) == 1
    assert tool_events[0]["result"] == tool_result

    # 12. 최초의 검증 실패 답변은 전달되지 않아야 한다.
    token_events = [
        event["text"]
        for event in events
        if event.get("type") == "agent_token"
    ]

    assert token_events == [valid_report]

    # 13. 최종 이벤트는 재검증에 통과한 답변이어야 한다.
    assert events[0]["type"] == "agent_start"
    assert events[-1]["type"] == "agent_end"
    assert events[-1]["response"] == valid_report



def test_executive_report_rejects_conflicting_recommendation():
    from app.systems.k_electronics.agents.advisory import (
        validate_mna_report,
    )

    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "뉴로엣지 경영진 종합 평가",
        "company": "뉴로엣지",
        "persona": "ALL",
    })

    assert result["found"] is True
    assert result["recommendation"] == "CONDITIONAL_PROCEED"

    inputs = result["inputs"]

    # 원본 데이터와 인수 조건을 모두 포함한 정상 보고서
    valid_report = "\n".join([
        f"인수 대상: {inputs['company']}",
        f"연매출: {inputs['financials']['revenue']}",
        f"영업이익률: {inputs['financials']['operating_margin']}",
        f"인수가격: {inputs['valuation']['asking']}",
        f"최대 고객: {inputs['customers']['largest']}",
        f"고객 비중: {inputs['customers']['largest_share']}",
        f"특허 분쟁: {inputs['technology']['ip_dispute']}",
        "CEO 관점: 전략적 적합성 검토",
        "CFO 관점: 수익성과 인수가격 위험 검토",
        "CTO 관점: 기술 및 특허 위험 검토",
        f"최종 권고: {result['recommendation_label']}",
        "인수 조건:",
        *result["conditions"],
        "종합 평가 의견",
        "뉴로엣지의 AI NPU 기술은 전략적 성장과 "
        "시장 진입 기간 단축에 도움이 될 수 있다. "
        "그러나 인수가격, 고객 집중도, 특허소송, "
        "핵심 인력 이탈 위험이 존재한다. "
        "따라서 가격 협상과 고객 계약 검증, "
        "특허 위험 확인 및 핵심 인력 유지 조건을 "
        "모두 충족하는 경우에만 인수를 추진해야 한다.",
    ])

    # 먼저 정상 보고서가 통과하는지 확인
    assert validate_mna_report(
        valid_report,
        result,
    )["valid"] is True

    # 정상 권고 뒤에 모순되는 최종 권고 추가
    invalid_report = (
        valid_report
        + "\n\n최종 권고: DO_NOT_PROCEED"
    )

    validation = validate_mna_report(
        invalid_report,
        result,
    )

    # 서로 모순되는 권고는 통과하면 안 된다.
    assert validation["valid"] is False
    assert "RECOMMENDATION_CONFLICT" in validation["missing"]



def test_executive_report_rejects_conflicting_operating_margin():
    from app.systems.k_electronics.agents.advisory import (
        validate_mna_report,
    )

    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "뉴로엣지 경영진 종합 평가",
        "company": "뉴로엣지",
        "persona": "ALL",
    })

    assert result["found"] is True
    assert result["mode"] == "executive_summary"

    inputs = result["inputs"]

    # 원본 Workflow 결과에 부합하는 정상 보고서
    valid_report = "\n".join([
        f"인수 대상: {inputs['company']}",
        f"연매출: {inputs['financials']['revenue']}",
        f"영업이익률: {inputs['financials']['operating_margin']}",
        f"인수가격: {inputs['valuation']['asking']}",
        f"최대 고객: {inputs['customers']['largest']}",
        f"고객 비중: {inputs['customers']['largest_share']}",
        f"특허 분쟁: {inputs['technology']['ip_dispute']}",
        "CEO 관점: 전략적 적합성 검토",
        "CFO 관점: 수익성과 가격 위험 검토",
        "CTO 관점: 기술 및 특허 위험 검토",
        f"최종 권고: {result['recommendation_label']}",
        "인수 조건:",
        *result["conditions"],
        "종합 평가 의견",
        "뉴로엣지의 AI NPU 설계 역량은 K전자의 전략적 성장과 "
        "시장 진입 기간 단축에 도움이 될 수 있다. "
        "하지만 높은 인수가격과 고객 집중도, 특허소송, "
        "영업적자로 인한 재무적 위험이 존재한다. "
        "따라서 가격 협상과 고객 계약 검증, "
        "특허 위험 확인 및 핵심인력 유지 조건을 "
        "충족한 경우에만 인수를 추진해야 한다.",
    ])

    # 정상 보고서는 검증 통과
    assert validate_mna_report(
        valid_report, result
    )["valid"] is True

    # 정상 수치와 모순되는 영업이익률을 추가
    invalid_report = valid_report.replace(
        "CFO 관점: 수익성과 가격 위험 검토",
        (
            "CFO 관점: 수익성과 가격 위험 검토\n"
            "영업이익률은 6.1%로 흑자를 기록하고 있다."
        ),
    )

    validation = validate_mna_report(
        invalid_report,
        result,
    )

    # 동일 지표에 서로 다른 수치가 있으면 검증 실패
    assert validation["valid"] is False
    assert "OPERATING_MARGIN_CONFLICT" in validation["missing"]



def test_executive_report_rejects_conflicting_asking_price():
    from app.systems.k_electronics.agents.advisory import (
        validate_mna_report,
    )

    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "뉴로엣지 경영진 종합 평가",
        "company": "뉴로엣지",
        "persona": "ALL",
    })

    assert result["found"] is True
    assert result["mode"] == "executive_summary"

    inputs = result["inputs"]

    # 정상 보고서 작성
    valid_report = "\n".join([
        f"인수 대상: {inputs['company']}",
        f"연매출: {inputs['financials']['revenue']}",
        f"영업이익률: {inputs['financials']['operating_margin']}",
        f"인수가격: {inputs['valuation']['asking']}",
        f"최대 고객: {inputs['customers']['largest']}",
        f"고객 비중: {inputs['customers']['largest_share']}",
        f"특허 분쟁: {inputs['technology']['ip_dispute']}",
        "CEO 관점: 전략적 적합성 검토",
        "CFO 관점: 인수가격과 재무 위험 검토",
        "CTO 관점: 기술 및 특허 위험 검토",
        f"최종 권고: {result['recommendation_label']}",
        "인수 조건:",
        *result["conditions"],
        "종합 평가 의견",
        "뉴로엣지의 AI NPU 기술은 전략적 성장과 "
        "시장 진입 기간 단축에 도움이 될 수 있다. "
        "하지만 인수가격, 고객 집중도, 특허소송, "
        "영업적자에 따른 재무적 위험이 존재한다. "
        "따라서 가격 협상과 고객 계약 검증, "
        "특허 위험 확인 및 핵심인력 유지 조건을 "
        "모두 충족해야 조건부 진행이 가능하다.",
    ])

    # 정상 보고서는 통과해야 한다.
    assert validate_mna_report(
        valid_report,
        result,
    )["valid"] is True

    # 잘못된 인수가격을 추가한다.
    invalid_report = valid_report.replace(
        "CFO 관점: 인수가격과 재무 위험 검토",
        (
            "CFO 관점: 인수가격과 재무 위험 검토\n"
            "인수가격은 3.5조 원으로 확인되었다."
        ),
    )

    validation = validate_mna_report(
        invalid_report,
        result,
    )

    # 올바른 가격과 잘못된 가격이 함께 있으면 실패해야 한다.
    assert validation["valid"] is False
    assert "ASKING_PRICE_CONFLICT" in validation["missing"]



def test_executive_report_rejects_conflicting_customer_share():
    from app.systems.k_electronics.agents.advisory import (
        validate_mna_report,
    )

    tool = make_run_mna_review(DATA_DIR / "companies.json")

    result = tool.invoke({
        "query": "뉴로엣지 경영진 종합 평가",
        "company": "뉴로엣지",
        "persona": "ALL",
    })

    assert result["found"] is True
    assert result["mode"] == "executive_summary"

    inputs = result["inputs"]
    customer = inputs["customers"]["largest"]
    customer_share = inputs["customers"]["largest_share"]

    # 1. 정상 보고서 작성
    valid_report = "\n".join([
        f"인수 대상: {inputs['company']}",
        f"연매출: {inputs['financials']['revenue']}",
        f"영업이익률: {inputs['financials']['operating_margin']}",
        f"인수가격: {inputs['valuation']['asking']}",
        f"최대 고객: {customer}",
        f"고객 매출 비중: {customer_share}",
        f"특허 분쟁: {inputs['technology']['ip_dispute']}",
        "CEO 관점: 전략적 적합성 검토",
        "CFO 관점: 수익성과 고객 집중 위험 검토",
        "CTO 관점: 기술 및 특허 위험 검토",
        f"최종 권고: {result['recommendation_label']}",
        "인수 조건:",
        *result["conditions"],
        "종합 평가 의견",
        "뉴로엣지의 AI NPU 기술은 전략적 성장과 "
        "시장 진입 기간 단축에 도움이 될 수 있다. "
        "하지만 높은 고객 집중도와 인수가격, "
        "특허소송 및 재무적 위험이 존재한다. "
        "따라서 고객 계약 검증과 가격 협상, "
        "특허 위험 확인 및 핵심인력 유지 조건을 "
        "모두 충족하는 경우에만 인수를 추진해야 한다.",
    ])

    # 2. 정상 보고서는 통과해야 한다.
    validation = validate_mna_report(
        valid_report,
        result,
    )

    assert validation["valid"] is True
    assert validation["missing"] == []

    # 3. 잘못된 고객 매출 비중을 추가한다.
    invalid_report = valid_report.replace(
        "CFO 관점: 수익성과 고객 집중 위험 검토",
        (
            "CFO 관점: 수익성과 고객 집중 위험 검토\n"
            f"{customer}의 고객 매출 비중은 "
            "52%로 확인되었다."
        ),
    )

    # 4. 원본 데이터와 비교하여 충돌 확인
    validation = validate_mna_report(
        invalid_report,
        result,
    )

    assert validation["valid"] is False
    assert "CUSTOMER_SHARE_CONFLICT" in validation["missing"]
