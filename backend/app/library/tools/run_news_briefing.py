"""run_news_briefing — agent-visible Tool over the news_briefing workflow (app/library/workflows/):
the News Agent's basic workflow, run end to end for one request.

Shared library: a system binds it to its demo fixture with make_run_news_briefing(path) and
registers the Tool in its own Registry. The LLM step uses the runtime's configured chat model
(LLM_PROVIDER / OLLAMA_MODEL / OLLAMA_API_KEY in backend/.env); without one the workflow's rules run.
The News Agent covers external news only: no meeting records or company review data here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from ...runtime.llm import LLMConfigError, get_chat_model
from ..workflows import news_briefing as workflow

TOOL_NAME = "run_news_briefing"


def _llm():
    try:
        model = get_chat_model()
    except LLMConfigError:
        return None

    def call(system: str, user: str) -> str:
        content = model.invoke([("system", system), ("human", user)]).content
        return content if isinstance(content, str) else str(content)

    return call


def make_run_news_briefing(fixture: Path) -> BaseTool:
    """Tool instance whose sources are the live web news and this system's demo fixture (data/news.json)."""

    @tool(TOOL_NAME)
    def run_news_briefing(query: str, purpose: str = "general", days: int = 7) -> dict[str, Any]:
        """요청한 주제(기업, 산업, 시장 동향, 규제, 인물 등 무엇이든)의 최근 뉴스를 외부 소스에서 수집해
    정규화하고, 중복을 제거해 같은 이야기를 주제(topic)로 묶어 중요도를 매긴 뒤, 주제별 요약·핵심 사실·긍정/부정과
    전체 흐름(trend)·시사점(so_what)·부정 기사 알림·매체별 논조를 반환한다.

    Args:
        query: 검색할 주제 1~3단어 (예: "삼성전자", "HBM 시장", "AI 반도체 수출 규제", "뉴로엣지"). 요청에 없는 단어는 넣지 않는다.
        purpose: 분석 목적. "general"(기본: 일반 동향) 또는 "risk"(주제마다 리스크 종류·수준·확인 사항 추가).
        days: 며칠 전 기사까지 볼지 (기본 7).
    """
        return workflow.run_news_briefing(  # not the Tool itself: it has the same name
            query, purpose=purpose, days=max(1, min(int(days), 30)),
            sources=[workflow.GoogleNewsSource(), workflow.FixtureSource(fixture)], llm=_llm(),
        )

    return run_news_briefing
