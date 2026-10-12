"""K Electronics Super Agent — delegates work to specialist agents in natural language.

    request → model → delegate_to_*(task) → model → delegate_to_*(task) → … → model → END
                           │                              │                       │
                           └─ runs a specialist agent     └─ runs another one     └─ final answer (no tool call)

The graph and run loop are the shared super-agent core in common.py; this
module only says who the agent is. Each specialist agent is exposed to the
model as one delegation Tool (app/capabilities/tools/) whose argument is the
natural-language task and whose result is only that agent's final answer.

Which Skills and Tools this agent gets is decided by the Capability Registry
(app/capabilities/registry.py), looked up by AGENT_ID. Two invocation
contexts exist (see common.run_super): "a2a" — another agent system's Super
Agent sent the request and the answer must be grounded in a delegation —
and "direct" — a user is chatting with K Super.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph.state import CompiledStateGraph

from ..capabilities import skills_for, tools_for
from .common import Invocation, SuperAgent, build_super_graph, compose_system_prompt, run_super

AGENT_ID = "k_super"

IDENTITY = """당신은 K전자 슈퍼에이전트(K Electronics Super Agent)다.
그룹 슈퍼에이전트 등 다른 Agent System이 A2A로 보낸 검토 요청이나 사용자의 요청을 수행하기 위해,
K전자의 전문 에이전트에게 업무를 자연어로 위임하고 그 결과를 종합해 K전자 차원의 최종 검토 결과를 회신한다.

출력 규칙:
- 최종 응답은 한국어로 작성한다. 고유명사, 기술명, Time-to-Market 같은 용어는 원문 표기를 유지해도 된다.
- 출력은 채팅창에 일반 텍스트로 표시된다. 마크다운 문법(#, **, 표, 코드블록)은 사용하지 말고,
  줄바꿈과 "- " 목록만 사용한다."""

# Allowed capability subset, from the registry.
SYSTEM_PROMPT = compose_system_prompt(IDENTITY, skills_for(AGENT_ID))
TOOLS = tools_for(AGENT_ID)
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

SPEC = SuperAgent(AGENT_ID, "K전자 슈퍼에이전트", SYSTEM_PROMPT, tuple(TOOLS))


@lru_cache(maxsize=1)
def build_k_super() -> CompiledStateGraph:
    return build_super_graph(SPEC)


def run_k_super(request: str, *, invocation: Invocation = "a2a") -> str:
    """Runs K Super on one request and returns its final answer; see common.run_super."""
    return run_super(SPEC, build_k_super(), request, invocation=invocation)
