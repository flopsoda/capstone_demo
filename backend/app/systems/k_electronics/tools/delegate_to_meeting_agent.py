"""delegate_to_meeting_agent — Tool Capability of K Electronics Super: handoff to this system's meeting agent.
Built with the library delegation factory; this module owns the agent-visible description."""

from __future__ import annotations

from ....library.tools.delegation import make_delegate_tool

# Same identifiers as ../agents/*.AGENT_ID and the frontend AgentId.
FROM_AGENT = "k_super"
TO_AGENT = "meeting"

DESCRIPTION = """미팅 에이전트(Meeting Agent)에게 업무를 자연어로 위임한다.
담당: 과거 사내 회의 기록 조회, 회의에서 정해진 기준·결정사항, 미완료 Action Item 확인,
다른 시스템(그룹 등)이 보낸 업무 요청의 접수와 그 진행 상황 회신,
요청에 명시된 미완료 Action Item의 담당자에게 진행 상황 · 지연 사유를 묻는 리마인더(진행 상황 확인) 메일 발송."""


def _runner():
    from ..agents.meeting import run_meeting_agent  # resolved at call time (circular import otherwise)

    return run_meeting_agent


delegate_to_meeting_agent = make_delegate_tool(name="delegate_to_meeting_agent", description=DESCRIPTION, from_agent=FROM_AGENT, to_agent=TO_AGENT, run=_runner)
