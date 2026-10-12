"""delegate_to_meeting_agent — Tool Capability of Group Super: handoff to this system's meeting agent.
Built with the library delegation factory; this module owns the agent-visible description."""

from __future__ import annotations

from ....library.tools.delegation import make_delegate_tool

# Same identifiers as ../agents/*.AGENT_ID and the frontend AgentId.
FROM_AGENT = "group_super"
TO_AGENT = "meeting"

DESCRIPTION = """그룹의 미팅 에이전트(Meeting Agent)에게 업무를 자연어로 위임한다.
담당: 그룹 차원의 회의 기록(그룹 투자심의위원회, 그룹 경영전략회의, 계열사 CEO 간담회) 조회, 그룹 회의에서 정해진 기준·결정사항,
미완료 Action Item 확인. 계열사 내부 회의 기록은 담당하지 않는다 (그것은 해당 계열사 슈퍼에이전트에게 A2A로 요청)."""


def _runner():
    from ..agents.meeting import run_meeting_agent  # resolved at call time (circular import otherwise)

    return run_meeting_agent


delegate_to_meeting_agent = make_delegate_tool(name="delegate_to_meeting_agent", description=DESCRIPTION, from_agent=FROM_AGENT, to_agent=TO_AGENT, run=_runner)
