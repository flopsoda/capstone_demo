"""delegate_to_advisory_agent — Tool Capability of Group Super: handoff to this system's advisory agent.
Built with the library delegation factory; this module owns the agent-visible description."""

from __future__ import annotations

from ....library.tools.delegation import make_delegate_tool

# Same identifiers as ../agents/*.AGENT_ID and the frontend AgentId.
FROM_AGENT = "group_super"
TO_AGENT = "advisory"

DESCRIPTION = """그룹의 경영자문 에이전트(Management Advisory Agent)에게 업무를 자연어로 위임한다.
담당: 그룹 차원에서 직접 추진하는 인수·투자 대상 기업의 재무 타당성, 전략적 가치, 기술적 의미, 주요 리스크를 검토하고
권고(진행 / 조건부 진행 / 보류)와 조건을 도출. 계열사가 추진 중인 인수안의 타당성은 담당하지 않는다 (해당 계열사 슈퍼에이전트에게 A2A로 요청).
검토에 반영할 맥락이 있으면 task에 함께 적는다. 특정 경영자 관점(CEO / CFO / CTO 중 하나)이 필요할 때만 지정한다."""


def _runner():
    from ..agents.advisory import run_advisory_agent  # resolved at call time (circular import otherwise)

    return run_advisory_agent


delegate_to_advisory_agent = make_delegate_tool(name="delegate_to_advisory_agent", description=DESCRIPTION, from_agent=FROM_AGENT, to_agent=TO_AGENT, run=_runner)
