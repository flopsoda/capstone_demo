"""Group 뉴스 에이전트 — same specialist core and Skill as K Electronics' news agent, bound to the Group's own data.

The graph and run loop are the shared specialist core (app/runtime/agent_core.py);
this module only says who the agent is (../souls/news.md). Its Skills and Tools come from this
system's Registry (../registry.py), looked up by AGENT_ID.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph.state import CompiledStateGraph

from ....runtime.agent_core import Invocation, Specialist, build_specialist_graph, compose_system_prompt, run_specialist
from ....runtime.souls import load_soul
from ..registry import skills_for, tools_for

AGENT_ID = "news"

IDENTITY = load_soul(__file__)  # who the agent is: ../souls/news.md

# Allowed capability subset, from this system's registry.
SYSTEM_PROMPT = compose_system_prompt(IDENTITY, skills_for(AGENT_ID))
TOOLS = tools_for(AGENT_ID)
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

SPEC = Specialist(AGENT_ID, "그룹 뉴스 에이전트", SYSTEM_PROMPT, tuple(TOOLS))


@lru_cache(maxsize=1)
def build_news_agent() -> CompiledStateGraph:
    return build_specialist_graph(SPEC)


def run_news_agent(task: str, *, invocation: Invocation = "delegation") -> str:
    """Handles one task; see agent_core.run_specialist for the invocation contexts."""
    return run_specialist(SPEC, build_news_agent(), task, invocation=invocation)
