"""run_company_risk_news_analysis — Tool Capability of the News Agent.

The analysis itself is the implementation workflow in
app/workflows/company_risk_news_analysis.py; this module is only the
agent-visible surface (name, description, argument schema).
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from ...workflows.company_risk_news_analysis import analyze_company_risk_news


@tool("run_company_risk_news_analysis")
def run_company_risk_news_analysis(query: str, company: str, topics: list[str] | None = None) -> dict[str, Any]:
    """외부 뉴스에서 특정 기업과 관련된 리스크 기사를 찾아, 리스크 항목별로 종류·수준(HIGH/MEDIUM/LOW)·근거 사실을
    구조화해서 반환한다.

    Args:
        query: 조사 목적을 담은 자연어 질의 (예: "뉴로엣지 인수 판단에 영향을 줄 수 있는 최근 외부 리스크").
        company: 조사 대상 기업명 (예: "뉴로엣지").
        topics: 우선 확인할 리스크 주제 목록 (예: ["주요 고객", "법적 분쟁", "핵심인력"]). 비우면 모든 리스크를 반환한다.
    """
    return analyze_company_risk_news(query, company, topics)
