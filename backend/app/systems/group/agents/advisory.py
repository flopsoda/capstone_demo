"""Group 경영자문 에이전트 — same specialist core and Skill as K Electronics' advisory agent, bound to the Group's own data.

The graph and run loop are the shared specialist core (app/runtime/agent_core.py);
this module only says who the agent is. Its Skills and Tools come from this
system's Registry (../registry.py), looked up by AGENT_ID.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph.state import CompiledStateGraph

from ....runtime.agent_core import Invocation, Specialist, build_specialist_graph, compose_system_prompt, run_specialist
from ..registry import skills_for, tools_for

AGENT_ID = "advisory"

IDENTITY = """당신은 K그룹(지주회사)의 경영자문 에이전트(Management Advisory Agent)다.
역할: 그룹 차원에서 추진하는 인수·투자 의사결정에 대해 재무적 타당성, 전략적 가치, 기술적 의미, 주요 리스크를 검토하고
경영진 관점(CEO / CFO / CTO)에서 권고와 조건을 제시한다. 일반적인 M&A 개념이나 경영 전략에 대한 질문에도 답한다.

출력 규칙:
- 최종 응답은 한국어로 작성한다. 고유명사, 기술명, Workflow 이름, Action Item, Time-to-Market 같은 용어는 원문 표기를 유지해도 된다.
- 출력은 채팅창에 일반 텍스트로 표시된다. 마크다운 문법(#, **, 표, 코드블록)은 사용하지 말고,
  줄바꿈과 "- " 목록만 사용한다."""

# Allowed capability subset, from this system's registry.
SYSTEM_PROMPT = compose_system_prompt(IDENTITY, skills_for(AGENT_ID))
TOOLS = tools_for(AGENT_ID)
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

# Gathers evidence (graph, meeting / news agents, calculations) and records a checked judgement before it answers.
ADVISORY_RECURSION_LIMIT = 40
SPEC = Specialist(AGENT_ID, "그룹 경영자문 에이전트", SYSTEM_PROMPT, tuple(TOOLS), recursion_limit=ADVISORY_RECURSION_LIMIT)


@lru_cache(maxsize=1)
def build_advisory_agent() -> CompiledStateGraph:
    return build_specialist_graph(SPEC)


def run_advisory_agent(task: str, *, invocation: Invocation = "delegation") -> str:
    """Handles one task; see agent_core.run_specialist for the invocation contexts."""
    return run_specialist(SPEC, build_advisory_agent(), task, invocation=invocation)
