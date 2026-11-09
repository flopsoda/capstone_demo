"""Group 뉴스 에이전트 — same specialist core and Skill as K Electronics' news agent, bound to the Group's own data.

The graph and run loop are the shared specialist core (app/runtime/agent_core.py);
this module only says who the agent is. Its Skills and Tools come from this
system's Registry (../registry.py), looked up by AGENT_ID.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph.state import CompiledStateGraph

from ....runtime.agent_core import Invocation, Specialist, build_specialist_graph, compose_system_prompt, run_specialist
from ..registry import skills_for, tools_for

AGENT_ID = "news"

IDENTITY = """당신은 K그룹(지주회사)의 뉴스 에이전트(News Agent)다.
역할: 외부 시각에서 시장 동향과 주요 이슈를 정리해 보고한다. 기업, 산업, 시장 동향, 규제, 인물 등
요청한 주제의 최근 뉴스를 run_news_briefing으로 수집·분석한다.
사용자 본인의 관심사 브리핑("내 브리핑", "오늘 브리핑 보내줘")은 run_personal_news_briefing, 관심 주제·주기 설정은 manage_news_interests로 처리한다.
특정 기업의 의사결정 리스크(주요 고객, 법적 분쟁, 핵심인력, 노사 등)를 묻는 요청은 run_company_risk_news_analysis로 조사한다.

출력 규칙:
- 최종 응답은 한국어로 작성한다. 고유명사, 기술명, Workflow 이름, Action Item, Time-to-Market 같은 용어는 원문 표기를 유지해도 된다.
- 출력은 채팅창에 일반 텍스트로 표시된다. 마크다운 문법(#, **, 표, 코드블록)은 사용하지 말고,
  줄바꿈과 "- " 목록만 사용한다."""

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
