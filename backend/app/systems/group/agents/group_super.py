"""Group Super Agent — serves the group's executives; asks subsidiaries' Super Agents
over A2A when their internal review is needed, then writes the group-level judgment.

    request → model → delegate_to_*(task) | request_k_electronics_review(request) → model → … → model → END

The graph and run loop are the shared super-agent core (app/runtime/agent_core.py);
this module only says who the agent is (../souls/group_super.md). Which Skills and Tools it has is decided
by this system's Registry (../registry.py), looked up by AGENT_ID.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph.state import CompiledStateGraph

from ....runtime.agent_core import Invocation, SuperAgent, build_super_graph, compose_system_prompt, run_super
from ....runtime.souls import load_soul
from .. import MEMORY
from ..registry import skills_for, tools_for

AGENT_ID = "group_super"

IDENTITY = load_soul(__file__)  # who the agent is: ../souls/group_super.md

# Allowed capability subset, from this system's registry.
SYSTEM_PROMPT = compose_system_prompt(IDENTITY, skills_for(AGENT_ID))
TOOLS = tools_for(AGENT_ID)
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

SPEC = SuperAgent(AGENT_ID, "그룹 슈퍼에이전트", SYSTEM_PROMPT, tuple(TOOLS), memory=MEMORY)


@lru_cache(maxsize=1)
def build_group_super() -> CompiledStateGraph:
    return build_super_graph(SPEC)


def run_group_super(request: str, *, invocation: Invocation = "direct") -> str:
    """Runs Group Super on one request and returns its final answer; see agent_core.run_super."""
    return run_super(SPEC, build_group_super(), request, invocation=invocation)
