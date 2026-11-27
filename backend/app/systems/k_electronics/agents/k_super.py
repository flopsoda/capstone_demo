"""K Electronics Super Agent — delegates work to specialist agents in natural language.

    request → model → delegate_to_*(task) → model → delegate_to_*(task) → … → model → END
                           │                              │                       │
                           └─ runs a specialist agent     └─ runs another one     └─ final answer (no tool call)

The graph and run loop are the shared super-agent core in app/runtime/agent_core.py; this
module only says who the agent is (../souls/k_super.md). Each specialist agent is exposed to the
model as one delegation Tool (../tools/) whose argument is the
natural-language task and whose result is only that agent's final answer.

Which Skills and Tools this agent gets is decided by the Capability Registry
(../registry.py), looked up by AGENT_ID. Two invocation
contexts exist (see agent_core.run_super): "a2a" — another agent system's Super
Agent sent the request and the answer must be grounded in a delegation —
and "direct" — a user is chatting with K Super.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph.state import CompiledStateGraph

from .. import MEMORY
from ..registry import skills_for, tools_for
from ....runtime.agent_core import Invocation, SuperAgent, build_super_graph, compose_system_prompt, run_super
from ....runtime.souls import load_soul

AGENT_ID = "k_super"

IDENTITY = load_soul(__file__)  # who the agent is: ../souls/k_super.md

# Allowed capability subset, from the registry.
SYSTEM_PROMPT = compose_system_prompt(IDENTITY, skills_for(AGENT_ID))
TOOLS = tools_for(AGENT_ID)
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

SPEC = SuperAgent(AGENT_ID, "K전자 슈퍼에이전트", SYSTEM_PROMPT, tuple(TOOLS), memory=MEMORY)


@lru_cache(maxsize=1)
def build_k_super() -> CompiledStateGraph:
    return build_super_graph(SPEC)


def run_k_super(request: str, *, invocation: Invocation = "a2a") -> str:
    """Runs K Super on one request and returns its final answer; see agent_core.run_super."""
    return run_super(SPEC, build_k_super(), request, invocation=invocation)
