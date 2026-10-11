"""run_meeting_context_retrieval — Tool Capability of the Meeting Agent.

The retrieval itself is the implementation workflow in
app/workflows/meeting_context_retrieval.py; this module is only the
agent-visible surface (name, description, argument schema).
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from ...workflows.meeting_context_retrieval import retrieve_meeting_context


@tool("run_meeting_context_retrieval")
def run_meeting_context_retrieval(query: str, topics: list[str] | None = None) -> dict[str, Any]:
    """회의 기록 저장소에서 요청과 관련된 과거 회의를 찾아, 각 회의의 결정사항(발언자별 기준)과
    아직 완료되지 않은 Action Item을 반환한다.

    Args:
        query: 찾고자 하는 내용을 담은 자연어 질의 (예: "뉴로엣지 인수 관련 전략회의 결정사항").
        topics: 관련 주제 키워드 목록 (예: ["NeuroEdge", "M&A", "AI NPU"]).
    """
    return retrieve_meeting_context(query, topics)
