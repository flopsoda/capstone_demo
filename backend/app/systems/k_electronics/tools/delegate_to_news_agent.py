"""delegate_to_news_agent — Tool Capability of K Electronics Super.

Same shape as delegate_to_meeting_agent: reports the handoff as
delegation_start / delegation_end and returns only the News Agent's final
answer; the specialist's own messages and tool calls never enter the
caller's history.
"""

from __future__ import annotations

from langchain_core.tools import tool

from ....runtime.events import emit

# Same identifiers as ../agents/*.AGENT_ID and the frontend AgentId.
FROM_AGENT = "k_super"
TO_AGENT = "news"


@tool
def delegate_to_news_agent(task: str) -> str:
    """뉴스 에이전트(News Agent)에게 업무를 자연어로 위임한다.
    담당: 외부 뉴스 기반의 기업 리스크 조사 — 주요 고객 관련 리스크, 법적 분쟁, 핵심인력 관련 리스크 등.

    Args:
        task: 뉴스 에이전트가 수행할 업무를 자연어로 서술한 문장 (한국어).
    """
    # Resolved at call time: the News Agent itself consumes the registry
    # (which holds this tool), so importing it at module level would be circular.
    from ..agents.news import run_news_agent

    emit({"type": "delegation_start", "from": FROM_AGENT, "to": TO_AGENT, "task": task})
    response = run_news_agent(task)
    emit({"type": "delegation_end", "from": FROM_AGENT, "to": TO_AGENT, "response": response})
    return response
