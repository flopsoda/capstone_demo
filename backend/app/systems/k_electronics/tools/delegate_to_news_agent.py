"""delegate_to_news_agent — Tool Capability of K Electronics Super: handoff to this system's news agent.
Built with the library delegation factory; this module owns the agent-visible description."""

from __future__ import annotations

from ....library.tools.delegation import make_delegate_tool

# Same identifiers as ../agents/*.AGENT_ID and the frontend AgentId.
FROM_AGENT = "k_super"
TO_AGENT = "news"

DESCRIPTION = """뉴스 에이전트(News Agent)에게 업무를 자연어로 위임한다.
담당: 외부 뉴스 기반의 기업 리스크 조사 — 주요 고객 관련 리스크, 법적 분쟁, 핵심인력 관련 리스크 등."""


def _runner():
    from ..agents.news import run_news_agent  # resolved at call time (circular import otherwise)

    return run_news_agent


delegate_to_news_agent = make_delegate_tool(name="delegate_to_news_agent", description=DESCRIPTION, from_agent=FROM_AGENT, to_agent=TO_AGENT, run=_runner)
