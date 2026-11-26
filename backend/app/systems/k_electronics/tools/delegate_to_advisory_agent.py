"""delegate_to_advisory_agent — Tool Capability of K Electronics Super: handoff to this system's advisory agent.
Built with the library delegation factory; this module owns the agent-visible description."""

from __future__ import annotations

from ....library.tools.delegation import make_delegate_tool

# Same identifiers as ../agents/*.AGENT_ID and the frontend AgentId.
FROM_AGENT = "k_super"
TO_AGENT = "advisory"

DESCRIPTION = """경영자문 에이전트(Management Advisory Agent)에게 업무를 자연어로 위임한다.
담당: 인수 대상 기업의 재무 타당성, 전략적 가치, 기술적 의미, 주요 리스크를 검토하고
권고(진행 / 조건부 진행 / 보류)와 조건을 도출. 회의 기록과 뉴스를 잇는 기업 지식 그래프로 과거 회의의 결정 · 기준과
최근 외부 사건(보도된 관계 변화)의 관계도 분석하고, 필요하면 미팅 · 뉴스 에이전트에게 직접 조회해 근거를 모은 뒤
DCF · 시나리오 계산을 바탕으로 CEO · CFO · CTO 관점과 최종 권고를 스스로 판단한다. 판단은 근거와 함께 저장되고, 종합 평가 · 재평가는
투자심의 보고서(PDF) 참조를 함께 돌려준다. 새 사건을 반영한 재평가, 가격 등 조건을 바꾼 What-if, 지난 평가 · 보고서 조회도 맡긴다.
판단에 쓰라고 회의 기록 · 뉴스를 미리 모아 넘길 필요는 없다. 사용자가 말한 조건(가격 · 관점 · 새 소식)은 task에 그대로 적는다."""


def _runner():
    from ..agents.advisory import run_advisory_agent  # resolved at call time (circular import otherwise)

    return run_advisory_agent


delegate_to_advisory_agent = make_delegate_tool(name="delegate_to_advisory_agent", description=DESCRIPTION, from_agent=FROM_AGENT, to_agent=TO_AGENT, run=_runner)
