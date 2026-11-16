"""run_company_risk_news_analysis — agent-visible Tool surface (name, description, argument schema)
over the company_risk_news_analysis workflow (app/library/workflows/).

Shared library: the system that owns the data binds it with
make_run_company_risk_news_analysis(store) and registers the Tool in its own Registry.
News is public, so the call does not depend on who asks. Every call runs the news pipeline for the
company and reads the system's news store (articles registered from its data file included); each
article's risk and stance toward the company come from its stored analysis of the same version, else
the model (news/analysis.py), else the rules. The result keeps the `risks[]` contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from ..workflows.company_risk_news_analysis import analyze_company_risk_news, risk_articles
from ..workflows.meeting_store import StoreUnavailable
from ..workflows.news.analysis import analyze_with_store
from ..workflows.news.feeds import DEFAULT_FEEDS, load_feeds
from ..workflows.news.fetch import Fetcher
from ..workflows.news.pipeline import gather, lookback_days
from ..workflows.news.store import NewsStore
from ..workflows.news.text import target as target_of
from . import run_news_briefing as briefing_tool  # its _llm(): one place for the runtime model + timeout

TOOL_NAME = "run_company_risk_news_analysis"
ANALYZED = 40  # articles the model reads per call of the Tool (the rest keep the keyword rules)


def _analysis_input(row: dict[str, Any]) -> dict[str, Any]:
    body, notice = row.get("body") or "", row.get("usage_notice")
    if body and not notice:
        text, basis = body[:600], "body"
    elif body:
        text, basis = body[:200], "lead"
    elif row.get("summary"):
        text, basis = row["summary"][:300], "snippet"
    else:
        text, basis = row["title"], "title"
    return {"id": row["id"], "title": row["title"], "outlet": row["outlet"], "text": text, "basis": basis}


def make_run_company_risk_news_analysis(store: NewsStore | None, *, feeds: Path = DEFAULT_FEEDS,
                                        fetcher: Fetcher | None = None) -> BaseTool:
    """Tool instance bound to one system's news store."""
    fetcher = fetcher or Fetcher()

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
        if store is None:
            return {"query": query, "company": company, "matched": 0, "risks": [],
                    "error": "이 system에는 뉴스 저장소가 연결되어 있지 않습니다."}
        try:
            found = gather(store, load_feeds(feeds), fetcher, company, days=lookback_days())
            rows = found["articles"]
            # The model reads the articles that name the company in the headline first, newest first.
            ranked = sorted(rows, key=lambda r: (company.lower() not in r["title"].lower(), -r["published_at"].timestamp()))
            analyses = analyze_with_store([_analysis_input(r) for r in ranked[:ANALYZED]], target_of(company), briefing_tool._llm(), store)
        except StoreUnavailable as e:
            return {"query": query, "company": company, "matched": 0, "risks": [], "error": str(e)}
        result = analyze_company_risk_news(query, company, topics, articles=risk_articles(rows, company, analyses))
        return {**result, "freshness": found["freshness"], "coverage": found["coverage"]}

    return run_company_risk_news_analysis
