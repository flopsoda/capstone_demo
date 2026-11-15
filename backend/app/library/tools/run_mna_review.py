"""run_mna_review — Tool Capability factory: M&A review (single perspective, CEO·CFO·CTO summary, What-if).

Agent-visible name / description / argument schema live here (source of truth); the work is
in library/workflows (mna_review · mna_executive_summary · mna_scenario). The Tool only picks
the workflow from its arguments. `path` is the company store of the system that binds it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from ..workflows.mna_executive_summary import review_executive_summary
from ..workflows.mna_review import review_acquisition
from ..workflows.mna_scenario import review_what_if

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
  
        changes = {
            "asking_trillion_krw": asking_trillion_krw,
            "operating_margin": operating_margin,
            "revenue_share": revenue_share,
        }
        has_scenario = any(value is not None for value in changes.values())

        # CEO·CFO·CTO 종합 평가
        if persona.upper() == "ALL":
            if has_scenario:
                return {
                    "error": "종합 평가(ALL)에서는 What-if 분석을 지원하지 않습니다.",
                    "supported_personas": ["CEO", "CFO", "CTO", "ALL"],
                }
            return review_executive_summary(query=query, company=company, context=context, path=path)

        # 변경 조건에 따른 What-if 비교
        if has_scenario:
            return review_what_if(query, company, persona, context, path=path, **changes)

        # 개별 관점 평가
        return review_acquisition(query, company, persona, context, path=path)

    return run_mna_review
