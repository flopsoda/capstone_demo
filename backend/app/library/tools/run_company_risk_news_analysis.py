"""run_company_risk_news_analysis — agent-visible Tool surface (name, description, argument schema)
over the company_risk_news_analysis workflow (app/library/workflows/).

Shared library: the system that owns the data binds it with
make_run_company_risk_news_analysis(path) and registers the Tool in its own Registry.
News is public, so the call does not depend on who asks. Next to the fixture the Tool reads
the system's collected store (news_collection), when there is one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from ..workflows.company_risk_news_analysis import analyze_company_risk_news
from ..workflows.news_collection import COLLECTED_FILE

TOOL_NAME = "run_company_risk_news_analysis"


def make_run_company_risk_news_analysis(path: Path) -> BaseTool:
    """Tool instance bound to one system's data store at `path` (data/news.json); the collected
    store is <its folder>/collected/news.json."""
    collected = path.parent / COLLECTED_FILE

    @tool(TOOL_NAME)
    def run_company_risk_news_analysis(query: str, company: str, topics: list[str] | None = None) -> dict[str, Any]:
        """외부 뉴스에서 특정 기업과 관련된 리스크 기사를 찾아, 리스크 항목별로 종류·수준(HIGH/MEDIUM/LOW)·근거 사실을
    구조화해서 반환한다. 같은 사안의 후속 보도는 한 리스크로 묶어 보도 흐름(timeline)·추세(trend)를 주고,
    리스크 종류별 확인 사항(check_points)과 우호적 보도(positive_signals)도 함께 반환한다.

    Args:
        query: 조사 목적을 담은 자연어 질의 (예: "뉴로엣지 인수 판단에 영향을 줄 수 있는 최근 외부 리스크").
        company: 조사 대상 기업명 (예: "뉴로엣지").
        topics: 우선 확인할 리스크 주제 목록 (예: ["주요 고객", "법적 분쟁", "핵심인력"]). 비우면 모든 리스크를 반환한다.
    """
        return analyze_company_risk_news(query, company, topics, path=path, collected=collected)

    return run_company_risk_news_analysis
