"""delegate_to_advisory_agent — Tool Capability of K Electronics Super.

Same shape as delegate_to_meeting_agent: reports the handoff as
delegation_start / delegation_end and returns only the Advisory Agent's
final answer; the specialist's own messages and tool calls never enter the
caller's history.
"""

from __future__ import annotations

from langchain_core.tools import tool

from ....runtime.events import emit

# Same identifiers as ../agents/*.AGENT_ID and the frontend AgentId.
FROM_AGENT = "k_super"
TO_AGENT = "advisory"


@tool
def delegate_to_advisory_agent(task: str) -> str:
    """경영자문 에이전트(Management Advisory Agent)에게 업무를 자연어로 위임한다.
    담당: 인수 대상 기업의 재무 타당성, 전략적 가치, 기술적 의미, 주요 리스크를 검토하고
    권고(진행 / 조건부 진행 / 보류)와 조건을 도출. 과거 회의에서 정한 기준이나 외부에서 확인된 리스크처럼
    검토에 반영해야 할 맥락이 있으면 task에 함께 적는다. 특정 경영자 관점(CEO / CFO / CTO 중 하나)이 필요할 때만 지정한다.

    Args:
        task: 경영자문 에이전트가 수행할 업무를 자연어로 서술한 문장 (한국어).
    """
    # Resolved at call time: the Advisory Agent itself consumes the registry
    # (which holds this tool), so importing it at module level would be circular.
    from ..agents.advisory import run_advisory_agent

    emit({"type": "delegation_start", "from": FROM_AGENT, "to": TO_AGENT, "task": task})
    response = run_advisory_agent(task)
    emit({"type": "delegation_end", "from": FROM_AGENT, "to": TO_AGENT, "response": response})
    return response
