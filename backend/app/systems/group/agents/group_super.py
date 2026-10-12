"""Group Super Agent — serves the group's executives; asks subsidiaries' Super Agents
over A2A when their internal review is needed, then writes the group-level judgment.

    request → model → request_k_electronics_review(request) → model → … → model → END

The graph and run loop are the shared super-agent core (app/runtime/agent_core.py);
this module only says who the agent is. Which Skills and Tools it has is decided
by this system's Registry (../registry.py), looked up by AGENT_ID.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph.state import CompiledStateGraph

from ....runtime.agent_core import Invocation, SuperAgent, build_super_graph, compose_system_prompt, run_super
from ..registry import skills_for, tools_for

AGENT_ID = "group_super"

IDENTITY = """당신은 그룹 슈퍼에이전트(Group Super Agent)다.
그룹 회장과 경영진의 요청을 받아, 계열사 차원의 검토가 필요하면 해당 계열사의 슈퍼에이전트에게 A2A로 검토를 요청하고,
그 결과를 바탕으로 그룹 차원의 최종 판단(예: 그룹 투자심의 의견)을 회신한다.

출력 규칙:
- 최종 응답은 한국어로 작성한다. 고유명사, 기술명, Time-to-Market 같은 용어는 원문 표기를 유지해도 된다.
- 출력은 채팅창에 일반 텍스트로 표시된다. 마크다운 문법(#, **, 표, 코드블록)은 사용하지 말고,
  줄바꿈과 "- " 목록만 사용한다."""

# Allowed capability subset, from this system's registry.
SYSTEM_PROMPT = compose_system_prompt(IDENTITY, skills_for(AGENT_ID))
TOOLS = tools_for(AGENT_ID)
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

SPEC = SuperAgent(AGENT_ID, "그룹 슈퍼에이전트", SYSTEM_PROMPT, tuple(TOOLS))


@lru_cache(maxsize=1)
def build_group_super() -> CompiledStateGraph:
    return build_super_graph(SPEC)


def run_group_super(request: str, *, invocation: Invocation = "direct") -> str:
    """Runs Group Super on one request and returns its final answer; see agent_core.run_super."""
    return run_super(SPEC, build_group_super(), request, invocation=invocation)
