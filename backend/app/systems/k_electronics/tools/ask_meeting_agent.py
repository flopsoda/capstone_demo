"""ask_meeting_agent — Tool Capability of K Electronics's advisory agent: a look-up-only handoff to this system's meeting agent.
Built with the library delegation factory (read_only: the meeting agent writes nothing during it)."""

from __future__ import annotations

from ....library.tools.delegation import make_delegate_tool

# Same identifiers as ../agents/*.AGENT_ID and the frontend AgentId.
FROM_AGENT = "advisory"
TO_AGENT = "meeting"

DESCRIPTION = """미팅 에이전트에게 회의 기록 조회를 맡긴다 (조회 전용: 기록 변경 · 메일 발송은 하지 않는다).
지식 그래프에 없거나 더 자세히 봐야 하는 것을 물을 때 쓴다: 회의에서 정한 결정 · 기준의 원문과 배경, 반대 의견,
후속 업무(Action Item)의 진행 상황 · 지연 사유 · 진행 메모, 초안 회의록. task에 대상 기업 · 안건과 확인할 항목을 쓰고,
답에 결정 ref(MTG-…-D1) · 업무 id를 함께 달라고 요청한다."""


def _runner():
    from ..agents.meeting import run_meeting_agent  # resolved at call time (circular import otherwise)

    return run_meeting_agent


ask_meeting_agent = make_delegate_tool(name="ask_meeting_agent", description=DESCRIPTION, from_agent=FROM_AGENT, to_agent=TO_AGENT,
                                       run=_runner, read_only=True)
