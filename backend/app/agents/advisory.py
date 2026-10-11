"""Management Advisory Agent — a LangGraph agent whose state is its own message history.

    request → model → (tool call?) → run_mna_review → model → answer

The graph and run loop are the shared specialist core in common.py; this
module only says who the agent is. Which Skills and Tools it has is decided
by the Capability Registry (app/capabilities/registry.py), looked up by AGENT_ID.

The persona (CEO / CFO / CTO) is not an agent: it is an evaluation-perspective
argument of run_mna_review that the model picks from the request.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph.state import CompiledStateGraph

from ..capabilities import skills_for, tools_for
from .common import Invocation, Specialist, build_specialist_graph, compose_system_prompt, run_specialist

AGENT_ID = "advisory"

IDENTITY = """당신은 K전자의 경영자문 에이전트(Management Advisory Agent)다.
역할: 인수·투자 같은 경영 의사결정에 대해 재무적 타당성, 전략적 가치, 기술적 의미, 주요 리스크를 검토하고
경영진 관점(CEO / CFO / CTO)에서 권고와 조건을 제시한다. 일반적인 M&A 개념이나 경영 전략에 대한 질문에도 답한다.

출력 규칙:
- 최종 응답은 한국어로 작성한다. 고유명사, 기술명, Workflow 이름, Time-to-Market 같은 용어는 원문 표기를 유지해도 된다.
- 출력은 채팅창에 일반 텍스트로 표시된다. 마크다운 문법(#, **, 표, 코드블록)은 사용하지 말고,
  줄바꿈과 "- " 목록만 사용한다."""

# Allowed capability subset, from the registry.
SYSTEM_PROMPT = compose_system_prompt(IDENTITY, skills_for(AGENT_ID))
TOOLS = tools_for(AGENT_ID)
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

SPEC = Specialist(AGENT_ID, "경영자문 에이전트", SYSTEM_PROMPT, tuple(TOOLS))


@lru_cache(maxsize=1)
def build_advisory_agent() -> CompiledStateGraph:
    return build_specialist_graph(SPEC)


def run_advisory_agent(task: str, *, invocation: Invocation = "delegation") -> str:
    """Handles one task; see common.run_specialist for the invocation contexts."""
    return run_specialist(SPEC, build_advisory_agent(), task, invocation=invocation)
