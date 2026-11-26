"""run_meeting_context_retrieval — agent-visible Tool surface (name, description, argument schema)
over the meeting_context_retrieval workflow (app/library/workflows/).

Shared library: the system that owns the data binds it with
make_run_meeting_context_retrieval(store) and registers the Tool in its own Registry.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from ..workflows.meeting_context_retrieval import retrieve_meeting_context
from ..workflows.meeting_store import MeetingStore
from . import evidence_items
from .meeting_access import current_viewer, store_call

TOOL_NAME = "run_meeting_context_retrieval"


def make_run_meeting_context_retrieval(store: MeetingStore) -> BaseTool:
    """Tool instance bound to one system's meeting store."""

    @tool(TOOL_NAME)
    def run_meeting_context_retrieval(
        query: str,
        topics: list[str] | None = None,
        meeting_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        """회의 기록 저장소에서 요청과 관련된 과거 회의(확정된 회의록)를 찾아, 각 회의의 요약, 결정사항
    (발언자별 기준 · 의결 결과, ref로 식별), 해결되지 않은 쟁점, 아직 완료되지 않은 Action Item(id · 담당 · 기한 ·
    지연 일수)을 반환한다. 요청자가 열람할 수 있는 등급의 회의만 조회된다.

    Args:
        query: 찾고자 하는 내용을 담은 자연어 질의 (예: "뉴로엣지 인수 관련 전략회의 결정사항").
        topics: 관련 주제 키워드 목록 (예: ["NeuroEdge", "M&A", "AI NPU"]).
        meeting_id: 특정 회의 하나를 id로 볼 때 (예: "MTG-2026-09-15"). 이 경우 초안 회의록도 반환한다.
        date_from: 이 날짜 이후 회의만 (YYYY-MM-DD).
        date_to: 이 날짜 이전 회의만 (YYYY-MM-DD).
    """
        viewer = current_viewer()
        return evidence_items.recorded(store_call(lambda: retrieve_meeting_context(
            query, topics, store=store, viewer=viewer, meeting_id=meeting_id, date_from=date_from, date_to=date_to,
        )), evidence_items.meeting_context)

    return run_meeting_context_retrieval
