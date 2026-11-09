"""run_news_briefing — agent-visible Tool over the news_briefing workflow (app/library/workflows/):
the News Agent's basic workflow, run end to end for one request.

Shared library: a system binds it to its demo fixture with make_run_news_briefing(path) and
registers the Tool in its own Registry. The LLM step uses the runtime's configured chat model
(LLM_PROVIDER / OLLAMA_MODEL / OLLAMA_API_KEY in backend/.env); without one the workflow's rules run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from ...runtime.llm import LLMConfigError, get_chat_model
from ..workflows import news_briefing as workflow
from ..workflows.meeting_context_retrieval import retrieve_meeting_context
from ..workflows.meeting_store import MeetingStore
from .meeting_access import current_viewer

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


def internal_context(query: str, data_dir: Path, meetings: MeetingStore | None,
                     org: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())) -> list[dict[str, Any]]:
    """What this company has on record about the query: meeting decisions, open issues and open
    Action Items the requester may see (their clearance, as the meeting Tools), and the review
    data of a company the query names. A store that is down only leaves meetings out.
    `org` = (names, topics): a query naming the organization itself ("K그룹") is about all of it,
    so its meetings are searched by the organization's meeting topics too ("계열사", "그룹")."""
    items = workflow.context_from_companies(data_dir / "companies.json", query)
    if meetings is not None:
        try:
            names, topics = org
            extra = list(topics) if any(n.lower() in query.lower() for n in names) else []
            found = retrieve_meeting_context(query, [query, *extra], store=meetings, viewer=current_viewer())
            items = workflow.context_from_meetings(found) + items
        except Exception:  # no database configured / reachable: the news still answers
            pass
    return items


def make_run_news_briefing(fixture: Path, meetings: MeetingStore | None = None,
                           org: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())) -> BaseTool:
    """Tool instance whose sources are the live web news and this system's demo fixture
    (data/news.json); internal context comes from the same data folder and meeting store."""
    data_dir = fixture.parent

    @tool(TOOL_NAME)
    def run_news_briefing(query: str, purpose: str = "general", days: int = 7) -> dict[str, Any]:
        """요청한 주제(기업, 산업, 시장 동향, 규제, 인물 등 무엇이든)의 최근 뉴스를 외부 소스에서 수집해
    정규화하고, 중복을 제거해 같은 이야기를 주제(topic)로 묶어 중요도를 매긴 뒤, 주제별 요약·핵심 사실·긍정/부정과
    전체 흐름(trend)·시사점(so_what)·부정 기사 알림·매체별 논조를 반환한다. 우리 회사 내부 기록(요청자가 볼 수 있는
    회의 결정사항·미결 쟁점·미완료 Action Item, 기업 검토 자료) 중 기사와 관련된 것을 주제마다 internal_links로 붙이고,
    같은 주제를 전에 조회했으면 그 뒤 새로 나온 이야기와 후속 보도(tracking, since_last)를 표시한다.

    Args:
        query: 검색할 주제 1~3단어 (예: "삼성전자", "HBM 시장", "AI 반도체 수출 규제", "뉴로엣지"). 요청에 없는 단어는 넣지 않는다.
        purpose: 분석 목적. "general"(기본: 일반 동향) 또는 "risk"(주제마다 리스크 종류·수준·확인 사항 추가).
        days: 며칠 전 기사까지 볼지 (기본 7).
    """
        return workflow.run_news_briefing(  # not the Tool itself: it has the same name
            query, purpose=purpose, days=max(1, min(int(days), 30)),
            sources=[workflow.GoogleNewsSource(), workflow.FixtureSource(fixture)], llm=_llm(),
            context=internal_context(query, data_dir, meetings, org),
            history=workflow.state_dir(data_dir) / "news_history.json",
        )

    return run_news_briefing
