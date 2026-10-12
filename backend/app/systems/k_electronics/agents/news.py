"""News Agent — a LangGraph agent whose state is its own message history.

    request → model → (tool call?) → run_company_risk_news_analysis → model → answer

The graph and run loop are the shared specialist core in app/runtime/agent_core.py; this
module only says who the agent is. Which Skills and Tools it has is decided
by the Capability Registry (../registry.py), looked up by AGENT_ID.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph.state import CompiledStateGraph

from ..registry import skills_for, tools_for
from ....runtime.agent_core import Invocation, Specialist, build_specialist_graph, compose_system_prompt, run_specialist

AGENT_ID = "news"

IDENTITY = """당신은 K전자의 뉴스 에이전트(News Agent)다.
역할: 외부 뉴스를 바탕으로 특정 기업과 관련된 리스크(주요 고객, 법적 분쟁, 핵심인력 등)를 조사하고,
현재 의사결정에 영향을 줄 수 있는 내용을 정리한다.

출력 규칙:
- 최종 응답은 한국어로 작성한다. 고유명사, 기술명, Workflow 이름 같은 용어는 원문 표기를 유지해도 된다.
- 출력은 채팅창에 일반 텍스트로 표시된다. 마크다운 문법(#, **, 표, 코드블록)은 사용하지 말고,
  줄바꿈과 "- " 목록만 사용한다."""

# Allowed capability subset, from the registry.
SYSTEM_PROMPT = compose_system_prompt(IDENTITY, skills_for(AGENT_ID))
TOOLS = tools_for(AGENT_ID)
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

SPEC = Specialist(AGENT_ID, "뉴스 에이전트", SYSTEM_PROMPT, tuple(TOOLS))


@lru_cache(maxsize=1)
def build_news_agent() -> CompiledStateGraph:
    return build_specialist_graph(SPEC)


def run_news_agent(task: str, *, invocation: Invocation = "delegation") -> str:
    """Handles one task; see agent_core.run_specialist for the invocation contexts."""
    return run_specialist(SPEC, build_news_agent(), task, invocation=invocation)
