"""run_meeting_context_retrieval — agent-visible Tool surface (name, description, argument schema)
over the meeting_context_retrieval workflow (app/library/workflows/).

Shared library: the system that owns the data binds it with
make_run_meeting_context_retrieval(path) and registers the Tool in its own Registry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from ..workflows.meeting_context_retrieval import retrieve_meeting_context

TOOL_NAME = "run_meeting_context_retrieval"


def make_run_meeting_context_retrieval(path: Path) -> BaseTool:
    """Tool instance bound to one system's data store at `path`."""

    @tool(TOOL_NAME)
    def run_meeting_context_retrieval(query: str, topics: list[str] | None = None) -> dict[str, Any]:
        """회의 기록 저장소에서 요청과 관련된 과거 회의를 찾아, 각 회의의 결정사항(발언자별 기준)과
    아직 완료되지 않은 Action Item을 반환한다.

    Args:
        query: 찾고자 하는 내용을 담은 자연어 질의 (예: "뉴로엣지 인수 관련 전략회의 결정사항").
        topics: 관련 주제 키워드 목록 (예: ["NeuroEdge", "M&A", "AI NPU"]).
    """
        return retrieve_meeting_context(query, topics, path=path)

    return run_meeting_context_retrieval
