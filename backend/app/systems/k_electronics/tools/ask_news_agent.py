"""ask_news_agent — Tool Capability of K Electronics's advisory agent: a look-up-only handoff to this system's news agent."""

from __future__ import annotations

from ....library.tools.delegation import make_delegate_tool

# Same identifiers as ../agents/*.AGENT_ID and the frontend AgentId.
FROM_AGENT = "advisory"
TO_AGENT = "news"

DESCRIPTION = """뉴스 에이전트에게 외부 뉴스 조사를 맡긴다 (조회 전용).
최신 정보가 필요하거나(새 소식 · 재평가 요청, 지식 그래프에 아직 없는 기사), 판단을 좌우하는 보도의 원문 · 리스크 분석을
확인할 때 쓴다(예: 최대 고객과의 계약 종료가 확정인지 검토 단계인지, 보도 범위와 매체별 논조). task에 대상 기업과 확인할 리스크 ·
사건을 쓰고, 답에 기사 id · 날짜 · 매체를 함께 달라고 요청한다."""


def _runner():
    from ..agents.news import run_news_agent  # resolved at call time (circular import otherwise)

    return run_news_agent


ask_news_agent = make_delegate_tool(name="ask_news_agent", description=DESCRIPTION, from_agent=FROM_AGENT, to_agent=TO_AGENT,
                                    run=_runner, read_only=True)
