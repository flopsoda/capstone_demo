
"""M&A 경영자문 Agent Tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool


from ..workflows.mna_review import (
    PERSONAS,
    RECOMMENDATION_LABELS,
    evaluate_all,
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
        """기업 인수 타당성을 평가하거나 변경 조건을 비교한다.

        Args:
            query: 사용자의 M&A 검토 질문.
            company: 인수 대상 기업명.
            persona: CEO, CFO, CTO 중 하나.
            context: 회의 내용 등 추가 맥락.
            asking_trillion_krw: 변경할 인수가격, 조 원 단위.
            operating_margin: 변경할 영업이익률, 0.05는 5%.
            revenue_share: 변경할 고객 매출 비중, 0.25는 25%.
        """

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

        result = compare_persona_scenarios(
            target,
            persona=persona,
            asking_trillion_krw=asking_trillion_krw,
            operating_margin=operating_margin,
            revenue_share=revenue_share,
        )

       
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
        assessments = evaluate_all(scenario_company, {})
        persona_key = result["persona"]
        priority = PERSONAS[persona_key]["criteria"]
        primary = [assessments[key] for key in priority]

        return {
            **result,

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
