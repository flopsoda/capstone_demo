"""Group 미팅 에이전트 — same specialist core and Skill as K Electronics' meeting agent, bound to the Group's own data.

The graph and run loop are the shared specialist core (app/runtime/agent_core.py);
this module only says who the agent is. Its Skills and Tools come from this
system's Registry (../registry.py), looked up by AGENT_ID.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph.state import CompiledStateGraph

from ....runtime.agent_core import Invocation, Specialist, build_specialist_graph, compose_system_prompt, run_specialist
from ..registry import skills_for, tools_for

AGENT_ID = "meeting"

IDENTITY = """당신은 K그룹(지주회사)의 미팅 에이전트(Meeting Agent)다.
역할: 그룹 차원의 회의 기록(그룹 투자심의위원회, 그룹 경영전략회의, 계열사 CEO 간담회 등)을 바탕으로
과거 회의에서 정해진 기준·결정사항과 아직 완료되지 않은 Action Item을 찾아 정리한다. 계열사 내부 회의 기록은 다루지 않는다.

출력 규칙:
- 최종 응답은 한국어로 작성한다. 고유명사, 기술명, Workflow 이름, Action Item, Time-to-Market 같은 용어는 원문 표기를 유지해도 된다.
- 출력은 채팅창에 일반 텍스트로 표시된다. 마크다운 문법(#, **, 표, 코드블록)은 사용하지 말고,
  줄바꿈과 "- " 목록만 사용한다."""

# Allowed capability subset, from this system's registry.
SYSTEM_PROMPT = compose_system_prompt(IDENTITY, skills_for(AGENT_ID))
TOOLS = tools_for(AGENT_ID)
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

SPEC = Specialist(AGENT_ID, "그룹 미팅 에이전트", SYSTEM_PROMPT, tuple(TOOLS))


@lru_cache(maxsize=1)
def build_meeting_agent() -> CompiledStateGraph:
    return build_specialist_graph(SPEC)


def run_meeting_agent(task: str, *, invocation: Invocation = "delegation") -> str:
    """Handles one task; see agent_core.run_specialist for the invocation contexts."""
    return run_specialist(SPEC, build_meeting_agent(), task, invocation=invocation)
