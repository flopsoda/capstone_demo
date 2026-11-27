"""Management Advisory Agent — a LangGraph agent whose state is its own message history.

    request → model → (tool call?) → run_mna_review → model → answer

The graph and run loop are the shared specialist core in app/runtime/agent_core.py; this
module only says who the agent is. Which Skills and Tools it has is decided
by the Capability Registry (../registry.py), looked up by AGENT_ID.

The persona (CEO / CFO / CTO) is not an agent: it is an evaluation-perspective
argument of run_mna_review that the model picks from the request.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph.state import CompiledStateGraph

from ..registry import skills_for, tools_for
from ....runtime.agent_core import Invocation, Specialist, build_specialist_graph, compose_system_prompt, run_specialist
from ....runtime.souls import load_soul

AGENT_ID = "advisory"

IDENTITY = load_soul(__file__)  # who the agent is: ../souls/advisory.md

# Allowed capability subset, from the registry.
SYSTEM_PROMPT = compose_system_prompt(IDENTITY, skills_for(AGENT_ID))
TOOLS = tools_for(AGENT_ID)
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

# Gathers evidence (graph, meeting / news agents, calculations) and records a checked judgement before it answers.
ADVISORY_RECURSION_LIMIT = 40
SPEC = Specialist(AGENT_ID, "경영자문 에이전트", SYSTEM_PROMPT, tuple(TOOLS), recursion_limit=ADVISORY_RECURSION_LIMIT)


@lru_cache(maxsize=1)
def build_advisory_agent() -> CompiledStateGraph:
    return build_specialist_graph(SPEC)


def run_advisory_agent(task: str, *, invocation: Invocation = "delegation") -> str:
    """Handles one task; see agent_core.run_specialist for the invocation contexts."""
    return run_specialist(SPEC, build_advisory_agent(), task, invocation=invocation)
