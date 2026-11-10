
"""M&A 경영자문 Agent Tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool
from ..workflows.mna_executive_summary import review_executive_summary


from ..workflows.mna_review import (
    PERSONAS,
    RECOMMENDATION_LABELS,
    evaluate_all,
    extract_context_signals,
    find_company,
    review_acquisition,
    summarize_inputs,
)
from ..workflows.mna_scenario import (
    apply_customer_scenario,
    apply_price_scenario,
    apply_profitability_scenario,
    compare_persona_scenarios,
)


TOOL_NAME = "run_mna_review"


def make_run_mna_review(path: Path) -> BaseTool:
    """M&A 평가 및 What-if 분석 Tool을 생성한다."""

    @tool(TOOL_NAME)
    def run_mna_review(
        query: str,
        company: str,
        persona: str = "CEO",
        context: str | None = None,
        asking_trillion_krw: float | None = None,
        operating_margin: float | None = None,
        revenue_share: float | None = None,
    ) -> dict[str, Any]:
        
        """인수 대상 기업의 재무·전략·기술 정보를 바탕으로 인수 타당성을 검토하고,
        선택한 경영자 관점(persona)의 평가 기준에 따라 평가 항목별 판단,
        긍정 요인, 우려 요인, 최종 권고 및 권고 조건을 구조화하여 반환한다.

        일반 평가에서는 CEO, CFO, CTO 중 선택한 관점을 적용한다.
        종합 평가(persona="ALL")에서는 세 경영진 관점을 통합한다.
        변경 조건이 제시된 경우에는 What-if 시나리오 비교를 수행한다.

        Args:
            query: 검토 목적을 담은 자연어 질의.
                예: "뉴로엣지 인수안의 본실사 진입 여부 검토".
            company: 인수 대상 기업명. 예: "뉴로엣지".
            persona: 평가 관점.
                CEO: 전략 적합성, 시장 진입 속도, 리스크와 기회 균형.
                CFO: 인수가격, 수익성, 고객 집중도, 재무 downside, 거래 조건.
                CTO: 기술 적합성, Time-to-Market, 핵심 기술인력,
                     IP 리스크, 기술 통합.
                ALL: CEO·CFO·CTO 통합 종합 평가.
                기본값은 CEO.
            context: 과거 회의에서 정한 기준, 외부에서 확인된 리스크,
                인수 조건 등을 자연어로 그대로 전달한다. 없으면 생략한다.
            asking_trillion_krw: 변경할 인수가격. 조 원 단위.
                예: 2.2조 원이면 2.2.
            operating_margin: 변경할 영업이익률.
                예: 5%이면 0.05.
            revenue_share: 변경할 최대 고객 매출 비중.
                예: 25%이면 0.25.

        Returns:
            일반 평가: 평가 항목별 판단, 긍정·우려 요인,
                권고(PROCEED / CONDITIONAL_PROCEED / DO_NOT_PROCEED),
                권고 조건.
            종합 평가: CEO·CFO·CTO 통합 판단 및 최종 권고.
            What-if 평가: 변경 전후 평가 결과와 권고 비교.

        Note:
            persona="ALL"과 What-if 변경 조건을 동시에 사용하는 것은
            현재 지원하지 않는다.
        """
  
        # CEO·CFO·CTO 종합 평가
        if persona.upper() == "ALL":
            if any(
                value is not None
                for value in (
                    asking_trillion_krw,
                    operating_margin,
                    revenue_share,
                )
            ):
                return {
                    "error": "종합 평가(ALL)에서는 What-if 분석을 지원하지 않습니다.",
                    "supported_personas": ["CEO", "CFO", "CTO", "ALL"],
                }

            return review_executive_summary(
                query=query,
                company=company,
                context=context,
                path=path,
            )

        
        has_scenario = any(
            value is not None
            for value in (
                asking_trillion_krw,
                operating_margin,
                revenue_share,
            )
        )

        # 기존 M&A 평가 유지
        if not has_scenario:
            return review_acquisition(
                query,
                company,
                persona,
                context,
                path=path,
            )

        # 기업 데이터 조회
        target = find_company(company, path)

        if target is None:
            return {
                "query": query,
                "company": company,
                "found": False,
                "message": f"'{company}'에 대한 기업 데이터가 없습니다.",
            }

        # Persona별 What-if 평가
        if persona.upper() not in ("CEO", "CFO", "CTO"):
            return {
                "error": f"지원하지 않는 persona: {persona!r}",
                "supported_personas": ["CEO", "CFO", "CTO"],
            }

        
        try:
            result = compare_persona_scenarios(
                target,
                persona=persona,
                asking_trillion_krw=asking_trillion_krw,
                operating_margin=operating_margin,
                revenue_share=revenue_share,
                context=context,
            )
        except ValueError as exc:
            return {
                "query": query,
                "company": target["name"],
                "found": True,
                "error": str(exc),
                "error_type": "INVALID_SCENARIO",
            }

       
        # 변경 조건을 기업 데이터 복사본에 적용
        scenario_company = target

        if asking_trillion_krw is not None:
            scenario_company = apply_price_scenario(
                scenario_company, asking_trillion_krw
            )

        if operating_margin is not None:
            scenario_company = apply_profitability_scenario(
                scenario_company, operating_margin
            )

        if revenue_share is not None:
            scenario_company = apply_customer_scenario(
                scenario_company, revenue_share
            )

        # 기존 UI에서 사용하는 평가 항목 생성
        assessments = evaluate_all(
            scenario_company,
            extract_context_signals(context),
        )
        persona_key = result["persona"]
        priority = PERSONAS[persona_key]["criteria"]
        primary = [assessments[key] for key in priority]

        
        return {
            **result,

            # What-if 변경 전후 원본 데이터
            "before_inputs": summarize_inputs(target),
            "after_inputs": summarize_inputs(scenario_company),

            # 기존 UI 호환 필드
            "query": query,


            # 기존 UI 호환 필드
            "query": query,
            "found": True,
            "persona_focus": [item["label"] for item in primary],
            "inputs": summarize_inputs(scenario_company),
            "assessments": primary,
            "recommendation": result["after_recommendation"],
            "recommendation_label": RECOMMENDATION_LABELS[
                result["after_recommendation"]
            ],
            "conditions": result["after_conditions"],

            # What-if 결과 구분
            "mode": "what_if",
            "notice": (
                "변경 조건에 따른 규칙 기반 M&A 분석 결과입니다."
            ),
        }


    return run_mna_review
