"""run_news_briefing — agent-visible Tool over the news_briefing workflow (app/library/workflows/):
the News Agent's basic workflow, run end to end for one request.

Shared library: a system binds it with make_run_news_briefing(store) — its news store — and
registers the Tool in its own Registry. Every call runs the news pipeline over the publisher feeds
and everything the store holds (articles the system registered from its data file included).
The LLM step uses the runtime's configured chat model; without one the rules run.
The News Agent covers external news only: no meeting records or company review data here.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from ...runtime.llm import LLMConfigError, get_chat_model
from ..workflows import news_briefing as workflow
from ..workflows.meeting_store import StoreUnavailable
from ..workflows.news.embed import default_embedder
from ..workflows.news.feeds import DEFAULT_FEEDS, load_feeds
from ..workflows.news.fetch import Fetcher
from ..workflows.news.pipeline import lookback_days
from ..workflows.news.store import NewsStore
from . import evidence_items

TOOL_NAME = "run_news_briefing"
LLM_TIMEOUT = 90  # seconds one analysis call may take before the rules answer instead
_LLM_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="news-llm")


def _llm():
    """(system, user) -> text over the runtime's chat model, or None when none is configured.
    A call that takes longer than LLM_TIMEOUT raises, and the workflow falls back to its rules."""
    try:
        model = get_chat_model()
    except LLMConfigError:
        return None

    def invoke(system: str, user: str) -> str:
        content = model.invoke([("system", system), ("human", user)]).content
        return content if isinstance(content, str) else str(content)

    def call(system: str, user: str) -> str:
        try:
            return _LLM_POOL.submit(invoke, system, user).result(timeout=LLM_TIMEOUT)
        except FutureTimeout as e:
            raise TimeoutError(f"LLM call exceeded {LLM_TIMEOUT}s") from e

    return call


def make_run_news_briefing(store: NewsStore | None, *, feeds: Path = DEFAULT_FEEDS, fetcher: Fetcher | None = None) -> BaseTool:
    """Tool instance: collects from `feeds` into `store` and answers from what `store` holds."""
    fetcher = fetcher or Fetcher()

    @tool(TOOL_NAME)
    def run_news_briefing(query: str, purpose: str = "general", days: int | None = None) -> dict[str, Any]:
        """요청한 주제(기업, 산업, 시장 동향, 규제, 인물 등 무엇이든)의 최근 뉴스를 언론사 RSS에서 수집하고 저장된 기사에서 키워드와 의미로 찾아,
    기사 본문을 정제하고 중복을 제거해 같은 사건을 주제(topic)로 묶어 중요도를 매긴 뒤, 주제별 요약·핵심 사실·긍정/부정과
    전체 흐름(trend)·시사점(so_what)·부정 기사 알림, 출처 기사와 수집 현황(freshness, coverage)을 반환한다.

    Args:
        query: 검색할 주제 (예: "삼성전자", "HBM 시장", "AI 반도체 수출 규제", "뉴로엣지 기술 경쟁력 관련 위험"). 요청에 없는 단어는 넣지 않는다.
        purpose: 분석 목적. "general"(기본: 일반 동향) 또는 "risk"(주제마다 리스크 종류·수준·확인 사항 추가).
        days: 며칠 전 기사까지 볼지 (비우면 기본 조회 기간 전체, 120일). 최근 며칠만 원하면 숫자를 준다.
    """
        longest = lookback_days()
        days = longest if days is None else max(1, min(int(days), longest))
        if store is None:
            return {"query": query, "error": "이 system에는 뉴스 저장소가 연결되어 있지 않습니다.", "topics": []}
        try:
            return evidence_items.recorded(workflow.run_news_briefing(  # the workflow, not this Tool (same name)
                query, store=store, feeds=load_feeds(feeds), fetcher=fetcher, purpose=purpose, days=days, llm=_llm(),
                embedder=default_embedder()), evidence_items.news_briefing)
        except StoreUnavailable as e:
            return {"query": query, "error": str(e), "topics": []}

    return run_news_briefing
