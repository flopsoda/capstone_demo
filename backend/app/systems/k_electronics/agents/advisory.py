"""Management Advisory Agent — a LangGraph agent whose state is its own message history.

    request → model → (tool call?) → run_mna_review → model → answer

The graph and run loop are the shared specialist core in app/runtime/agent_core.py; this
module only says who the agent is. Which Skills and Tools it has is decided
by the Capability Registry (../registry.py), looked up by AGENT_ID.

The persona (CEO / CFO / CTO) is not an agent: it is an evaluation-perspective
argument of run_mna_review that the model picks from the request.
"""
from __future__ import annotations

import re
from ....runtime.events import AgentError, collect_events, emit
from typing import Any
from functools import lru_cache

from langgraph.graph.state import CompiledStateGraph

from ..registry import skills_for, tools_for
from ....runtime.agent_core import (
    Invocation,
    Specialist,
    build_specialist_graph,
    compose_system_prompt,
    run_specialist,
    message_text,
)

import json

from langchain_core.messages import HumanMessage, SystemMessage
from ....runtime.llm import get_chat_model

AGENT_ID = "advisory"


IDENTITY = """당신은 K전자의 경영자문 에이전트(Management Advisory Agent)다.
역할: 인수·투자 같은 경영 의사결정에 대해 재무적 타당성, 전략적 가치, 기술적 의미, 주요 리스크를 검토하고
경영진 관점(CEO / CFO / CTO)에서 권고와 조건을 제시한다. 일반적인 M&A 개념이나 경영 전략에 대한 질문에도 답한다.

출력 규칙:
- 최종 응답은 한국어로 작성한다. 고유명사, 기술명, Workflow 이름, Time-to-Market 같은 용어는 원문 표기를 유지해도 된다.
- 출력은 채팅창에 일반 텍스트로 표시된다. 마크다운 문법(#, **, 표, 코드블록)은 사용하지 말고,
  줄바꿈과 "- " 목록만 사용한다.

추가 지침 — 경영자문 분석:
- 특정 기업의 M&A 타당성 평가를 요청받으면 반드시 등록된 run_mna_review Tool을 사용한다.
- 일반적인 M&A 개념이나 경영 전략 질문에는 Tool 없이 답변할 수 있다.
- 사용자의 요청에서 기업명, 평가 관점, 변경 조건을 정확하게 파악한다.
- 종합적인 CEO·CFO·CTO 검토 요청은 persona="ALL"을 사용한다.
- 개별 경영진 관점의 질문에는 해당 persona를 선택한다.
- 사용자가 인수가격, 영업이익률 또는 고객 비중을 변경하는 경우에는 해당 What-if 인자를 정확하게 전달한다.
- 현재 persona="ALL"에서는 What-if 분석을 지원하지 않으므로 변경 조건이 있으면 적절한 개별 persona를 선택한다.
- 데이터가 없는 기업은 사실을 만들어내지 않고 데이터 부족을 안내한다.

추가 지침 — LLM 최종 답변:
- 최종 경영자문 보고서는 당신이 직접 작성한다.
- Python Workflow Tool은 데이터 조회, 재무 계산, 위험 평가 및 판단 근거를 제공한다.
- Tool의 분석 결과를 단순히 나열하지 말고 경영 의사결정에 어떤 의미가 있는지 설명한다.
- 도구 결과에 없는 추가 분석이나 제안은 검증된 사실과 명확하게 구분한다.
- Tool의 최종 권고안과 위험 등급을 임의로 변경하지 않는다.

추가 지침 — 사실 정확성:
- 기업명, 고객명, 특허 분쟁 상대 등 고유명사를 Tool 결과와 동일하게 유지한다.
- 매출액, 인수가격, 적정가치, 수익률 등의 수치와 단위를 정확하게 유지한다.
- 특히 조 원, 억 원, 만 원을 혼동하지 않는다.
- 매출 CAGR과 영업이익률을 서로 다른 지표로 구분한다.
- Time-to-Market 단축 기간을 실제 시장 출시 시점으로 바꾸지 않는다.
- 확인되지 않은 계약 기간, 재무 수치, 특허 수, 인수 조건 등을 임의로 생성하지 않는다.
- 기존 인수 조건은 변경하거나 누락하지 않는다.
- 새로운 의견이 필요한 경우 '추가 검토 제안'으로 구분한다.
- 불확실한 내용은 사실처럼 단정하지 않는다.
- 최종 답변 작성 후 고유명사, 금액, 위험 등급, 인수 조건이 Tool 결과와 일치하는지 점검한다.

추가 지침 — 경영진 관점:
- CEO: 전략적 적합성, 시장 진입 효과, 사업 가치와 성장 가능성을 설명한다.
- CFO: 기업가치, 인수가격, 수익성, 재무 위험과 거래 조건을 설명한다.
- CTO: 기술 적합성, 특허 위험, 핵심 인력 유지 및 기술 통합 가능성을 설명한다.
- 종합 평가에서는 세 관점을 구분해 설명하되 최종 권고는 하나로 정리한다.
- 종합 평가에서 positive, concern, neutral 등의
  상태값만 나열하지 않는다.
- 각 평가 항목에 대해 판단 결과, 구체적인 근거,
  경영 의사결정에 미치는 의미를 자연어로 설명한다.
- Tool이 제공한 finding과 evidence를 활용하되,
  확인되지 않은 사실이나 수치는 추가하지 않는다.
- 위험 수준(HIGH, MEDIUM, LOW)은 정확하게 유지하고,
  평가 결과는 긍정적, 우려, 중립 등의 한국어로 설명한다.
- 최종 권고에서는 주요 인수 기회와 핵심 위험을
  연결하여 해당 권고에 도달한 이유를 설명한다.

추가 지침 — 종합 보고서 필수 검증:
- 종합 보고서 첫 부분에 반드시 인수 대상 기업명을 명시한다.
  기업명은 Tool 결과의 company 값을 그대로 사용한다.
- Tool 결과의 conditions에 포함된 모든 인수 조건을
  원문 그대로 출력한다.
  조건의 조사, 표현, 수치, 고유명사를 임의로 변경하지 않는다.
- CEO, CFO, CTO의 평가를 각각 구분하여 작성한다.
  각 관점의 주요 판단 근거와 우려 사항을 설명한다.
- 최종 권고는 Tool의 recommendation 결과와 일치해야 한다.
- 보고서를 완성하기 전에 기업명, 인수 조건,
  평가 관점 및 권고가 누락되지 않았는지 확인한다.

추가 지침 — 종합 보고서:
- 기업 및 거래 개요
- 재무 및 기업가치 분석
- CEO 전략적 평가
- CFO 재무적 평가
- CTO 기술적 평가
- 핵심 위험과 인수 조건
- 종합 권고 및 판단 근거

추가 지침 — 최종 종합 평가 의견:
- CEO·CFO·CTO 종합 평가에서는 보고서 마지막에
  "최종 권고"와 "종합 평가 의견"을 구분하여 작성한다.
- 최종 권고에는 Tool의 recommendation 결과를 정확하게 표시한다.
- 종합 평가 의견에는 다음 내용을 자연스러운 문장으로 설명한다.
  1) CEO 관점에서 인수를 추진할 전략적 이유
  2) CFO 관점에서 인수 결정을 제한하는 재무적 위험
  3) CTO 관점에서 기술 확보 가치와 해결해야 할 기술적 위험
  4) 세 관점을 종합한 최종 판단과 그 이유
  5) 인수 진행을 위해 우선 충족해야 할 핵심 조건
- 단순히 평가 항목이나 위험 등급을 반복해서 나열하지 않는다.
- Tool의 executive_reviews, key_opportunities, key_risks,
  decision_rationale을 근거로 경영진의 의사결정에
  실질적으로 도움이 되는 종합 의견을 작성한다.
- Tool의 verified_report를 그대로 복사하는 것으로
  최종 보고서 작성을 대신하지 않는다.
- 종합 평가 의견은 구체적인 판단 근거를 포함한
  3~5개의 완결된 문장으로 작성한다.
- Tool에서 확인되지 않은 사실이나 수치를 만들어내지 않는다.
- 일반적인 질문이나 개별 Persona 질문에는
  종합 평가 보고서 형식을 강제하지 않는다.


- 사용자가 특정 항목만 질문한 경우 전체 보고서를 강제하지 않고 해당 질문에 집중한다.
- What-if 분석은 변경 전후의 조건과 평가 결과를 명확하게 구분한다.
- 가상기업 데이터와 실제 기업 공시 데이터의 출처를 혼동하지 않는다.
"""


# Allowed capability subset, from the registry.
SYSTEM_PROMPT = compose_system_prompt(IDENTITY, skills_for(AGENT_ID))
TOOLS = tools_for(AGENT_ID)
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def validate_mna_report(
    report: str,
    tool_result: dict[str, Any],
) -> dict[str, Any]:
    """LLM 보고서를 실제 M&A Tool 결과와 대조한다."""

    if not tool_result.get("found"):
        return {
            "valid": False,
            "reason": "TOOL_DATA_NOT_FOUND",
            "missing": [],
        }

    if not isinstance(report, str) or not report.strip():
        return {
            "valid": False,
            "reason": "EMPTY_REPORT",
            "missing": [],
        }

    inputs = tool_result.get("inputs") or {}
    financials = inputs.get("financials") or {}
    valuation = inputs.get("valuation") or {}
    customers = inputs.get("customers") or {}
    technology = inputs.get("technology") or {}

    # 띄어쓰기와 쉼표 차이만 허용한다.
    def normalize(value: Any) -> str:
        text = str(value or "")
        text = text.replace(",", "").replace("−", "-")
        return re.sub(r"\s+", "", text).lower()

    normalized_report = normalize(report)
    missing = []

    facts = {
        "COMPANY": inputs.get("company"),
        "REVENUE": financials.get("revenue"),
        "OPERATING_MARGIN": financials.get("operating_margin"),
        "ASKING_PRICE": valuation.get("asking"),
        "CUSTOMER": customers.get("largest"),
        "CUSTOMER_SHARE": customers.get("largest_share"),
    }

    for name, expected in facts.items():
        if expected is None:
            continue

        if normalize(expected) not in normalized_report:
            missing.append(name)

            
    # 동일한 영업이익률 지표에 서로 다른 수치가
    # 기재되어 있는지 확인한다.
    expected_margin = financials.get("operating_margin")

    if expected_margin is not None:
        # "영업이익률: -8.2%"
        # "영업이익률은 6.1%"
        # "영업이익률 -8.2%"
        # 형식을 지원한다.
        margin_pattern = (
            r"영업\s*이익률\s*"
            r"(?:은|는|이|가|:|：|=)?\s*"
            r"([+\-−]?\s*\d+(?:\.\d+)?\s*%)"
        )

        reported_margins = re.findall(
            margin_pattern,
            report,
        )

        normalized_expected = normalize(expected_margin)

        if any(
            normalize(margin) != normalized_expected
            for margin in reported_margins
        ):
            missing.append("OPERATING_MARGIN_CONFLICT")


    # 인수가격 충돌 검증
    # 협상 기준가격 및 적정가치는 별도 지표로 취급한다.
    expected_price = valuation.get("asking")

    if expected_price is not None:
        price_pattern = (
            r"(?<![가-힣])인수\s*가격\s*"
            r"(?:은|는|이|가|:|：|=)?\s*"
            r"(\d+(?:\.\d+)?\s*조\s*원)"
        )

        reported_prices = re.findall(
            price_pattern,
            report,
        )

        normalized_expected = normalize(expected_price)

        if any(
            normalize(price) != normalized_expected
            for price in reported_prices
        ):
            missing.append("ASKING_PRICE_CONFLICT")

    
    # 최대 고객의 매출 비중 충돌 검증
    # 다른 재무 비율이나 타 고객의 비중과 혼동하지 않는다.
    expected_customer = customers.get("largest")
    expected_share = customers.get("largest_share")

    if expected_customer and expected_share is not None:
        # "고객 매출 비중: 46%"
        # "알파모바일의 고객 매출 비중은 46%"
        # "알파모바일 매출 비중 46%"
        # 형식을 지원한다.
        customer_share_pattern = (
            r"(?:고객\s*)?매출\s*비중"
            r"\s*(?:은|는|이|가|:|：|=)?\s*"
            r"(\d+(?:\.\d+)?\s*%)"
        )

        reported_shares = re.findall(
            customer_share_pattern,
            report,
        )

        normalized_expected = normalize(expected_share)

        if any(
            normalize(share) != normalized_expected
            for share in reported_shares
        ):
            missing.append("CUSTOMER_SHARE_CONFLICT")


    # 특허 분쟁 상대는 Tool의 구조화된 입력에서 추출한다.
    ip_dispute = technology.get("ip_dispute")

    if isinstance(ip_dispute, str):
        counterparty = ip_dispute.split("·")[0].strip()
        if counterparty and counterparty != "없음":
            if normalize(counterparty) not in normalized_report:
                missing.append("PATENT_PARTY")

    
    # 최종 권고 코드 또는 한국어 표현 확인
    recommendation = tool_result.get("recommendation")
    recommendation_label = tool_result.get("recommendation_label")

    if recommendation:
        if (
            normalize(recommendation) not in normalized_report
            and normalize(recommendation_label) not in normalized_report
        ):
            missing.append("RECOMMENDATION")

    # 명시적으로 작성된 최종 권고의 충돌 여부 확인
    # 다른 분석 항목에서 언급한 권고는 제외하고,
    # '최종 권고:'에 표시된 코드만 검사한다.
    declared_recommendations = re.findall(
        r"(?im)^\s*(?:#{1,6}\s*)?"
        r"(?:\*\*)?\s*최종\s*권고\s*(?:\*\*)?\s*"
        r"[:：]\s*(?:\*\*)?\s*"
        r"(CONDITIONAL_PROCEED|DO_NOT_PROCEED|PROCEED)\b",
        report,
    )

    # Tool의 최종 권고와 다른 코드가 명시되어 있으면 실패
    if recommendation and any(
        declared.upper() != str(recommendation).upper()
        for declared in declared_recommendations
    ):
        missing.append("RECOMMENDATION_CONFLICT")


    # 원본 조건의 의미를 임의로 추측하지 않는다.
    # 조건이 존재하면 핵심 용어가 누락됐는지 점검한다.
    conditions = tool_result.get("conditions") or []

    for index, condition in enumerate(conditions, start=1):
        if normalize(condition) not in normalized_report:
            missing.append(f"CONDITION_{index}")


    # 종합 M&A 보고서의 구성 및 최종 판단 근거 확인
    if tool_result.get("mode") == "executive_summary":

        # CEO, CFO, CTO 관점별 평가 내용이 포함되었는지 확인
        for persona in ("CEO", "CFO", "CTO"):
            pattern = (
                rf"\b{persona}\b"
                rf"\s*(?:(?:전략적|재무적|기술적)\s*)?"
                rf"(?:관점|평가)"
            )

            if not re.search(pattern, report, re.IGNORECASE):
                missing.append(f"{persona}_VIEW")

        # 마지막 종합 의견 또는 최종 권고 부분 확인
        conclusion_headers = list(re.finditer(
            r"종합\s*(?:권고|판단|의견|평가)|최종\s*권고",
            report,
        ))

        conclusion = (
            report[conclusion_headers[-1].end():].strip()
            if conclusion_headers else ""
        )

        # 긍정 요인, 위험 요인, 조건의 설명 여부 확인
        has_opportunity = any(
            word in conclusion
            for word in ("전략", "성장", "기술", "시장", "시너지")
        )

        has_risk = any(
            word in conclusion
            for word in ("위험", "리스크", "가격", "고객", "손실")
        )

        has_condition = any(
            word in conclusion
            for word in ("조건", "검증", "충족", "확보")
        )

        if not (
            len(normalize(conclusion)) >= 80
            and has_opportunity
            and has_risk
            and has_condition
        ):
            missing.append("FINAL_RATIONALE")


    return {
        "valid": not missing,
        "reason": "PASS" if not missing else "FACT_CHECK_FAILED",
        "missing": missing,
        "checked_conditions": len(conditions),
    }



def validate_individual_mna_report(
    report: str,
    tool_result: dict[str, Any],
) -> dict[str, Any]:
    """개별 CEO/CFO/CTO 보고서를 Workflow 결과와 대조한다."""

    if not tool_result.get("found"):
        return {
            "valid": False,
            "reason": "TOOL_DATA_NOT_FOUND",
            "missing": [],
        }

    if not isinstance(report, str) or not report.strip():
        return {
            "valid": False,
            "reason": "EMPTY_REPORT",
            "missing": [],
        }

    persona = str(tool_result.get("persona") or "").upper()

    if persona not in ("CEO", "CFO", "CTO"):
        return {
            "valid": False,
            "reason": "INVALID_PERSONA",
            "missing": [],
        }

    inputs = tool_result.get("inputs") or {}
    financials = inputs.get("financials") or {}
    valuation = inputs.get("valuation") or {}
    customers = inputs.get("customers") or {}

    def normalize(value: Any) -> str:
        text = str(value or "")
        text = text.replace(",", "").replace("−", "-")
        return re.sub(r"\s+", "", text).lower()

    normalized_report = normalize(report)
    missing = []

    # 기업 정보와 주요 재무 수치 확인
    facts = {
        "COMPANY": inputs.get("company"),
        "REVENUE": financials.get("revenue"),
        "OPERATING_MARGIN": financials.get("operating_margin"),
        "ASKING_PRICE": valuation.get("asking"),
        "CUSTOMER": customers.get("largest"),
        "CUSTOMER_SHARE": customers.get("largest_share"),
    }

    for name, expected in facts.items():
        if expected is None:
            continue

        if normalize(expected) not in normalized_report:
            missing.append(name)

    # 요청한 Persona가 보고서에 포함됐는지 확인
    if not re.search(rf"\b{persona}\b", report, re.IGNORECASE):
        missing.append("PERSONA")

    # 최종 권고 일치 여부 확인
    recommendation = tool_result.get("recommendation")
    recommendation_label = tool_result.get("recommendation_label")

    if recommendation:
        if (
            normalize(recommendation) not in normalized_report
            and normalize(recommendation_label) not in normalized_report
        ):
            missing.append("RECOMMENDATION")

    # 기존 Workflow에서 생성한 인수 조건 확인
    conditions = tool_result.get("conditions") or []

    for index, condition in enumerate(conditions, start=1):
        if normalize(condition) not in normalized_report:
            missing.append(f"CONDITION_{index}")

    return {
        "valid": not missing,
        "reason": "PASS" if not missing else "FACT_CHECK_FAILED",
        "missing": missing,
        "checked_conditions": len(conditions),
    }


def validate_what_if_report(
    report: str,
    tool_result: dict[str, Any],
) -> dict[str, Any]:
    """What-if 보고서를 Workflow의 변경 전후 결과와 대조한다."""

    if not tool_result.get("found"):
        return {
            "valid": False,
            "reason": "TOOL_DATA_NOT_FOUND",
            "missing": [],
        }

    if tool_result.get("mode") != "what_if":
        return {
            "valid": False,
            "reason": "INVALID_MODE",
            "missing": [],
        }

    if not isinstance(report, str) or not report.strip():
        return {
            "valid": False,
            "reason": "EMPTY_REPORT",
            "missing": [],
        }

    def normalize(value: Any) -> str:
        text = str(value or "")
        text = text.replace(",", "").replace("−", "-")
        return re.sub(r"\s+", "", text).lower()

    normalized_report = normalize(report)
    missing = []

    # 1. 기업명과 Persona 확인
    company = tool_result.get("company")
    persona = str(tool_result.get("persona") or "").upper()

    if company and normalize(company) not in normalized_report:
        missing.append("COMPANY")

    if persona in ("CEO", "CFO", "CTO"):
        if normalize(persona) not in normalized_report:
            missing.append("PERSONA")

    # 2. 변경 조건의 전후 수치 확인
    assumptions = tool_result.get("assumptions") or {}
    before_inputs = tool_result.get("before_inputs") or {}
    after_inputs = tool_result.get("after_inputs") or {}

    scenario_fields = {
        "asking_trillion_krw": (
            "valuation", "asking", "PRICE"
        ),
        "operating_margin": (
            "financials", "operating_margin", "MARGIN"
        ),
        "revenue_share": (
            "customers", "largest_share", "CUSTOMER_SHARE"
        ),
    }

    for argument, (section, field, label) in scenario_fields.items():
        if assumptions.get(argument) is None:
            continue

        before_value = (
            before_inputs.get(section) or {}
        ).get(field)

        after_value = (
            after_inputs.get(section) or {}
        ).get(field)

        if (
            before_value is None
            or normalize(before_value) not in normalized_report
        ):
            missing.append(f"BEFORE_{label}")

        if (
            after_value is None
            or normalize(after_value) not in normalized_report
        ):
            missing.append(f"AFTER_{label}")

    # 3. 변경 전후 권고 확인
    before_recommendation = tool_result.get(
        "before_recommendation"
    )
    after_recommendation = tool_result.get(
        "after_recommendation"
    )

    for label, recommendation in (
        ("BEFORE_RECOMMENDATION", before_recommendation),
        ("AFTER_RECOMMENDATION", after_recommendation),
    ):
        if (
            not recommendation
            or normalize(recommendation) not in normalized_report
        ):
            missing.append(label)

    # 4. 변경 후 인수 조건 원문 확인
    after_conditions = tool_result.get("after_conditions") or []

    for index, condition in enumerate(after_conditions, start=1):
        if normalize(condition) not in normalized_report:
            missing.append(f"AFTER_CONDITION_{index}")

    # 5. 추가되거나 제거된 조건의 정확성 확인
    for label, key in (
        ("REMOVED_CONDITION", "removed_conditions"),
        ("ADDED_CONDITION", "added_conditions"),
    ):
        for index, condition in enumerate(
            tool_result.get(key) or [], start=1
        ):
            if normalize(condition) not in normalized_report:
                missing.append(f"{label}_{index}")

    return {
        "valid": not missing,
        "reason": "PASS" if not missing else "FACT_CHECK_FAILED",
        "missing": missing,
        "checked_conditions": len(after_conditions),
    }


SPEC = Specialist(AGENT_ID, "경영자문 에이전트", SYSTEM_PROMPT, tuple(TOOLS))


@lru_cache(maxsize=1)

def build_advisory_agent() -> CompiledStateGraph:
    return build_specialist_graph(SPEC)


def run_advisory_agent(
    task: str,
    *,
    invocation: Invocation = "delegation",
) -> str:
    """경영자문 실행 후 검증된 결과만 전달한다."""

    # 1. 기존 공통 Agent 실행 기능을 그대로 사용한다.
    # 단, 검증 전까지 이벤트를 임시 보관한다.
    
    # 모델이 잘못된 입력 형식을 생성한 경우에만 한 번 재시도한다.
    for attempt in range(2):
        try:
            response, events = collect_events(
                lambda: run_specialist(
                    SPEC,
                    build_advisory_agent(),
                    task,
                    invocation=invocation,
                )
            )
            break

        except AgentError as exc:
            cause = exc.__cause__

            # ValidationError가 아니면 재시도하지 않는다.
            if type(cause).__name__ != "ValidationError":
                raise

            # 두 번째 실행에서도 실패하면 오류를 그대로 전달한다.
            if attempt == 1:
                raise


    # 2. 실제 Tool Calling 결과를 확인한다.
    tool_results = [
        event.get("result")
        for event in events
        if event.get("type") == "tool_end"
        and event.get("tool") == "run_mna_review"
    ]

    # 3. Tool 자체에서 오류가 발생한 경우
    # 정상적인 분석 보고서로 취급하지 않는다.
    for result in tool_results:
        
        if isinstance(result, dict) and result.get("error"):
            raise AgentError(
                AGENT_ID,
                f"M&A 분석 도구 오류: {result['error']}"
            )


    # 4. 종합 M&A 평가 결과를 선택한다.
    executive_results = [
        result
        for result in tool_results
        if isinstance(result, dict)
        and result.get("found") is True
        and result.get("mode") == "executive_summary"
    ]



    # 5. 종합 평가 보고서 사실 검증
    if executive_results:
        tool_result = executive_results[-1]
        validation = validate_mna_report(response, tool_result)

        if not validation["valid"]:
            missing = validation.get("missing", [])

            # 최초 실행에서 확인된 Tool 결과를 재사용한다.
            # Workflow Tool은 다시 호출하지 않는다.
            correction_task = (
                f"원래 사용자 요청:\n{task}\n\n"
                "이전 종합 M&A 보고서가 검증에 실패했습니다.\n"
                f"누락 항목: {', '.join(missing)}\n\n"
                "아래에 제공하는 것은 이미 실행하고 확인한 "
                "run_mna_review Tool의 결과입니다.\n"
                "Tool을 다시 호출하지 말고 이 결과만 근거로 "
                "종합 보고서를 새로 작성하세요.\n\n"
                "작성 요구사항:\n"
                "1. 원래 사용자 요청의 목적과 평가 관점을 유지하세요.\n"
                "2. 기업명과 재무 수치를 정확히 유지하세요.\n"
                "3. CEO 전략적 평가, CFO 재무적 평가, "
                "CTO 기술적 평가를 구분하세요.\n"
                "4. 인수 조건은 Tool의 conditions에 있는 "
                "문구를 그대로 모두 포함하세요.\n"
                "5. 최종 권고는 Tool의 recommendation과 "
                "일치해야 합니다.\n"
                "6. 마지막에 '종합 평가 의견'을 작성하고 "
                "기회, 위험, 판단 근거, 선행 조건을 "
                "3~5개의 완결된 문장으로 설명하세요.\n"
                "7. verified_report를 그대로 복사하지 말고 "
                "분석 근거를 종합한 자연어 보고서를 작성하세요.\n"
                "8. 검증된 데이터에 없는 사실을 만들지 마세요.\n"
                "9. 최종 보고서만 출력하세요.\n\n"
                "확인된 M&A Tool 결과:\n"
                + json.dumps(
                    tool_result,
                    ensure_ascii=False,
                    default=str,
                )
            )

            # Tool을 바인딩하지 않은 LLM으로
            # 보고서 문장만 다시 생성한다.
            try:
                revised_message = get_chat_model().invoke([
                    SystemMessage(
                        content=(
                            "당신은 K전자의 M&A 종합 보고서 작성자입니다. "
                            "제공된 검증 데이터만 사용해 한국어로 작성하세요. "
                            "외부 정보를 추측하거나 도구를 호출하지 마세요. "
                            "기업명, 수치, 권고와 인수 조건을 변경하지 마세요. "
                            "사용자에게 최종 보고서만 전달하세요."
                        )
                    ),
                    HumanMessage(content=correction_task),
                ])

                revised_response = message_text(revised_message)

            except Exception as exc:
                raise AgentError(
                    AGENT_ID,
                    f"종합 보고서 재작성 오류: {exc}",
                ) from exc

            # 빈 보고서 또는 사실 검증 실패 시 전달하지 않는다.
            revalidation = validate_mna_report(
                revised_response,
                tool_result,
            )

            if not revalidation["valid"]:
                raise AgentError(
                    AGENT_ID,
                    "종합 보고서 재검증 실패: "
                    + ", ".join(revalidation.get("missing", [])),
                )

            # 검증된 원본 Tool 호출 이벤트만 유지한다.
            start_event = next(
                (
                    e for e in events
                    if e.get("type") == "agent_start"
                ),
                None,
            )

            end_event = next(
                (
                    e for e in reversed(events)
                    if e.get("type") == "agent_end"
                ),
                None,
            )

            if start_event is None or end_event is None:
                raise AgentError(
                    AGENT_ID,
                    "Agent 실행 이벤트가 누락되었습니다.",
                )

            tool_events = [
                e for e in events
                if e.get("type") in ("tool_start", "tool_end")
            ]

            # 검증 전 보고서 토큰을 전달하지 않고,
            # 검증에 통과한 최종 보고서만 전달한다.
            events = [
                start_event,
                *tool_events,
                {
                    "type": "agent_token",
                    "agent": AGENT_ID,
                    "text": revised_response,
                },
                {
                    **end_event,
                    "response": revised_response,
                },
            ]

            response = revised_response


    
    # 6. 개별 Persona 보고서 사실 검증
    individual_results = [
        result
        for result in tool_results
        if isinstance(result, dict)
        and result.get("found") is True
        and result.get("mode") not in (
            "executive_summary",
            "what_if",
        )
        and str(result.get("persona", "")).upper()
        in ("CEO", "CFO", "CTO")
    ]

    if individual_results and not executive_results:
        tool_result = individual_results[-1]

        validation = validate_individual_mna_report(
            response,
            tool_result,
        )

        if not validation["valid"]:
            missing = validation.get("missing", [])

            # 최초 Workflow 결과를 유지하고
            # LLM 보고서만 다시 작성한다.
           
            # 최초 Workflow에서 확정한 Persona와 조건을 유지한다.
            persona = str(tool_result.get("persona") or "").upper()
            conditions = tool_result.get("conditions") or []

            exact_conditions = "\n".join(
                f"- {condition}"
                for condition in conditions
            )

            correction_task = (
                f"원래 사용자 요청:\n{task}\n\n"
                "이전 개별 M&A 보고서가 사실 검증에 실패했습니다.\n"
                f"누락 항목: {', '.join(missing)}\n\n"

                "다음은 반드시 지켜야 하는 작성 규칙입니다.\n"
                f"1. 보고서 첫 줄에 '{persona} 관점 M&A 평가'를 "
                "정확히 작성하세요.\n"
                "2. 기업명, 매출액, 영업이익률, 인수가격, "
                "최대 고객 및 고객 비중을 원본대로 유지하세요.\n"
                "3. 최종 권고는 Workflow 결과와 일치해야 합니다.\n"
                "4. 아래 인수 조건은 단어, 띄어쓰기, 고유명사, "
                "수치까지 변경하지 않고 그대로 복사하세요.\n"
                "5. 조건을 의역하거나 다른 문장으로 "
                "바꿔 쓰지 마세요.\n"
                "6. 새로운 인수 조건을 임의로 추가하지 마세요.\n"
                "7. 새로운 경영적 제안이 필요하다면 "
                "'추가 검토 제안'으로 별도 구분하세요.\n"
                "8. 보고서 형식은 권고, 핵심 판단 근거, "
                "주요 우려, 권고 조건 순서로 작성하세요.\n"
                "9. 일반 텍스트로 작성하고 Markdown 표, "
                "굵은 글씨, 코드블록은 사용하지 마세요.\n\n"

                "반드시 원문 그대로 포함할 인수 조건:\n"
                f"{exact_conditions}\n\n"

                "다음은 최초 실행에서 확인한 M&A Workflow "
                "결과입니다. Tool을 재실행하지 마세요.\n"
                + json.dumps(
                    tool_result,
                    ensure_ascii=False,
                    default=str,
                )
            )

            try:
                revised_message = get_chat_model().invoke([
                    SystemMessage(
                        content=(
                            "당신은 K전자의 경영자문 보고서 작성자입니다. "
                            "이미 검증된 Workflow 결과만 사용하세요. "
                            f"현재 평가 관점은 {persona}입니다. "
                            "보고서에 해당 Persona를 명시하세요. "
                            "인수 조건은 원문 그대로 출력해야 하며 "
                            "의역하거나 수정하면 안 됩니다. "
                            "새로운 사실이나 조건을 만들어내지 마세요. "
                            "최종 응답은 한국어 일반 텍스트로 작성하세요."
                        )
                    ),
                    HumanMessage(content=correction_task),
                ])

                revised_response = message_text(
                    revised_message
                )

            except Exception as exc:
                raise AgentError(
                    AGENT_ID,
                    f"개별 M&A 보고서 재작성 오류: {exc}",
                ) from exc


            revalidation = validate_individual_mna_report(
                revised_response,
                tool_result,
            )

        
            if not revalidation["valid"]:
                raise AgentError(
                    AGENT_ID,
                    "개별 M&A 보고서 재검증 실패: "
                    + ", ".join(
                        revalidation.get("missing", [])
                    ),
                )


            start_event = next(
                (
                    event for event in events
                    if event.get("type") == "agent_start"
                ),
                None,
            )

            end_event = next(
                (
                    event for event in reversed(events)
                    if event.get("type") == "agent_end"
                ),
                None,
            )

            if start_event is None or end_event is None:
                raise AgentError(
                    AGENT_ID,
                    "Agent 실행 이벤트가 누락되었습니다.",
                )

            tool_events = [
                event
                for event in events
                if event.get("type") in (
                    "tool_start",
                    "tool_end",
                )
            ]

            # 검증 실패한 답변 대신
            # 재검증에 통과한 답변만 전달한다.
            events = [
                start_event,
                *tool_events,
                {
                    "type": "agent_token",
                    "agent": AGENT_ID,
                    "text": revised_response,
                },
                {
                    **end_event,
                    "response": revised_response,
                },
            ]

            response = revised_response

    
    # 7. What-if 보고서 사실 검증
    what_if_results = [
        result
        for result in tool_results
        if isinstance(result, dict)
        and result.get("found") is True
        and result.get("mode") == "what_if"
    ]

    if what_if_results:
        tool_result = what_if_results[-1]

        validation = validate_what_if_report(
            response,
            tool_result,
        )

        if not validation["valid"]:
            missing = validation.get("missing", [])

            # 최초 Workflow의 변경 조건과 결과를 유지한다.
            persona = str(
                tool_result.get("persona") or ""
            ).upper()

            before = tool_result.get("before_inputs") or {}
            after = tool_result.get("after_inputs") or {}

            after_conditions = (
                tool_result.get("after_conditions") or []
            )
            removed_conditions = (
                tool_result.get("removed_conditions") or []
            )
            added_conditions = (
                tool_result.get("added_conditions") or []
            )

            correction_task = (
                f"원래 사용자 요청:\n{task}\n\n"
                "이전 What-if 보고서가 사실 검증에 실패했습니다.\n"
                f"누락 항목: {', '.join(missing)}\n\n"
                "다음의 검증된 Workflow 결과만 사용하여 "
                "보고서를 다시 작성하세요.\n"
                "Workflow Tool을 다시 호출하지 마세요.\n\n"

                f"기업명: {tool_result.get('company')}\n"
                f"평가 관점: {persona}\n"
                f"변경 가정: {tool_result.get('assumptions')}\n"
                f"변경 전 입력값: {json.dumps(before, ensure_ascii=False)}\n"
                f"변경 후 입력값: {json.dumps(after, ensure_ascii=False)}\n"

                "변경 전 권고: "
                f"{tool_result.get('before_recommendation')}\n"
                "변경 후 권고: "
                f"{tool_result.get('after_recommendation')}\n\n"

                "변경 후 인수 조건 (문구 변경 금지):\n"
                + "\n".join(
                    f"- {condition}"
                    for condition in after_conditions
                )
                + "\n\n제거된 인수 조건 (문구 변경 금지):\n"
                + "\n".join(
                    f"- {condition}"
                    for condition in removed_conditions
                )
                + "\n\n추가된 인수 조건 (문구 변경 금지):\n"
                + "\n".join(
                    f"- {condition}"
                    for condition in added_conditions
                )
                + "\n\n작성 규칙:\n"
                "1. 기업명과 평가 관점을 명시하세요.\n"
                "2. 사용자가 변경한 조건의 변경 전후 수치를 정확히 표시하세요.\n"
                "3. 변경 전후 평가 결과와 위험 변화를 구분하세요.\n"
                "4. 변경 전후 권고를 각각 정확하게 표시하세요.\n"
                "5. 인수 조건은 원문을 그대로 복사하세요.\n"
                "6. 존재하지 않는 추가 조건은 만들지 마세요.\n"
                "7. 최종 권고가 유지되거나 변경된 이유를 설명하세요.\n"
                "8. 확인되지 않은 사실은 추가하지 마세요.\n"
                "9. 최종 보고서만 출력하세요.\n\n"
                "전체 Workflow 결과:\n"
                + json.dumps(
                    tool_result,
                    ensure_ascii=False,
                    default=str,
                )
            )

            # Tool을 재호출하지 않고 LLM으로 보고서만 재작성
            try:
                revised_message = get_chat_model().invoke([
                    SystemMessage(
                        content=(
                            "당신은 K전자의 M&A What-if 분석 보고서 작성자입니다. "
                            "이미 검증된 Workflow 결과만 사용하세요. "
                            "원본 기업 데이터와 변경 조건을 유지하세요. "
                            "변경 전후 권고를 구분하세요. "
                            "인수 조건은 단어와 띄어쓰기까지 원문대로 작성하세요. "
                            "Workflow Tool을 다시 호출하지 마세요. "
                            "한국어 일반 텍스트로 답하고 "
                            "마크다운 표나 코드블록은 사용하지 마세요."
                        )
                    ),
                    HumanMessage(content=correction_task),
                ])

                revised_response = message_text(
                    revised_message
                )

            except Exception as exc:
                raise AgentError(
                    AGENT_ID,
                    f"What-if 보고서 재작성 오류: {exc}",
                ) from exc

            # 재작성된 보고서를 최초 Tool 결과와 다시 대조
            revalidation = validate_what_if_report(
                revised_response,
                tool_result,
            )

            if not revalidation["valid"]:
                raise AgentError(
                    AGENT_ID,
                    "What-if 보고서 재검증 실패: "
                    + ", ".join(
                        revalidation.get("missing", [])
                    ),
                )

            # 기존 Tool 호출 이벤트를 유지하고
            # 검증된 최종 보고서만 전달한다.
            start_event = next(
                (
                    event for event in events
                    if event.get("type") == "agent_start"
                ),
                None,
            )

            end_event = next(
                (
                    event for event in reversed(events)
                    if event.get("type") == "agent_end"
                ),
                None,
            )

            if start_event is None or end_event is None:
                raise AgentError(
                    AGENT_ID,
                    "Agent 실행 이벤트가 누락되었습니다.",
                )

            tool_events = [
                event
                for event in events
                if event.get("type") in (
                    "tool_start",
                    "tool_end",
                )
            ]

            events = [
                start_event,
                *tool_events,
                {
                    "type": "agent_token",
                    "agent": AGENT_ID,
                    "text": revised_response,
                },
                {
                    **end_event,
                    "response": revised_response,
                },
            ]

            response = revised_response

    # 8. 기존 이벤트 시스템을 통해 결과 전달
    for event in events:
        emit(event)

    return response



