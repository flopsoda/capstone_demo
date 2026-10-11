"""delegate_to_meeting_agent — Tool Capability of K Electronics Super.

Delegation is an agent-to-agent handoff, so the tool reports it as
delegation_start / delegation_end (not as generic tool events) and returns
only the Meeting Agent's final answer; the specialist's own messages and tool
calls never enter the caller's history.
"""

from __future__ import annotations

from langchain_core.tools import tool

from ...events import emit

# Same identifiers as app/agents/*.AGENT_ID and the frontend AgentId.
FROM_AGENT = "k_super"
TO_AGENT = "meeting"


@tool
def delegate_to_meeting_agent(task: str) -> str:
    """미팅 에이전트(Meeting Agent)에게 업무를 자연어로 위임한다.
    담당: 과거 사내 회의 기록 조회, 회의에서 정해진 기준·결정사항, 미완료 Action Item 확인.

    Args:
        task: 미팅 에이전트가 수행할 업무를 자연어로 서술한 문장 (한국어).
    """
    # Resolved at call time: the Meeting Agent itself consumes the registry
    # (which holds this tool), so importing it at module level would be circular.
    from ...agents.meeting import run_meeting_agent

    emit({"type": "delegation_start", "from": FROM_AGENT, "to": TO_AGENT, "task": task})
    response = run_meeting_agent(task)
    emit({"type": "delegation_end", "from": FROM_AGENT, "to": TO_AGENT, "response": response})
    return response
