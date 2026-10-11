"""run_mna_review — Tool Capability of the Management Advisory Agent.

The review itself is the implementation workflow in app/workflows/mna_review.py;
this module is only the agent-visible surface (name, description, argument schema).
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from ...workflows.mna_review import review_acquisition


@tool("run_mna_review")
def run_mna_review(query: str, company: str, persona: str = "CEO", context: str | None = None) -> dict[str, Any]:
    """인수 대상 기업의 재무·전략·기술 정보를 바탕으로 인수 타당성을 검토하고, 선택한 경영자 관점(persona)의
    평가 기준에 따라 평가 항목별 판단, 긍정 요인, 우려 요인, 권고(PROCEED / CONDITIONAL_PROCEED / DO_NOT_PROCEED),
    권고 조건을 구조화해서 반환한다.

    Args:
        query: 검토 목적을 담은 자연어 질의 (예: "뉴로엣지 인수안의 본실사 진입 여부 검토").
        company: 인수 대상 기업명 (예: "뉴로엣지").
        persona: 평가 관점. "CEO"(전략 적합성·시장 진입 속도·리스크/기회 균형), "CFO"(가격·수익성·고객 집중도·재무 downside·거래 조건),
            "CTO"(기술 적합성·Time-to-Market·핵심 기술인력·IP 리스크·기술 통합) 중 하나. 기본값 CEO.
        context: 검토에 반영할 맥락 (과거 회의에서 정한 기준, 외부에서 확인된 리스크, 조건 등)을 자연어로 그대로 전달한다. 없으면 생략.
    """
    return review_acquisition(query, company, persona, context)
