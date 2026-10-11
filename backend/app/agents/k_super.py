"""K Electronics Super Agent — delegates work to specialist agents in natural language.

    A2A request → model → delegate_to_meeting_agent(task) → END
                                   │
                                   └─ invokes the Meeting Agent graph

Each specialist agent is exposed to the model as one tool. The tool's argument
is the natural-language task; its implementation runs the specialist's own
graph and returns only that agent's final answer, so the specialist's internal
tool calls never enter K Super's history.

Step 3 scope: a single delegation round. The graph ends after the tool node;
synthesis of specialist results (Step 6) will close the tools → model loop.
"""

from __future__ import annotations

import contextvars
from functools import lru_cache
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..llm import get_chat_model
from .common import RECURSION_LIMIT, AgentRun, execute_tool_calls
from .meeting import run_meeting_agent

AGENT_ID = "k_super"

# Full specialist traces for the current run (set per run_k_super call; the
# tool runs in the same thread, so a ContextVar keeps runs isolated).
_delegations: contextvars.ContextVar[list[AgentRun] | None] = contextvars.ContextVar(
    "k_super_delegations", default=None
)


@tool
def delegate_to_meeting_agent(task: str) -> str:
    """미팅 에이전트(Meeting Agent)에게 업무를 자연어로 위임한다.
    담당: 과거 사내 회의 기록 조회, 회의에서 정해진 기준·결정사항, 미완료 Action Item 확인.

    Args:
        task: 미팅 에이전트가 수행할 업무를 자연어로 서술한 문장 (한국어).
    """
    run = run_meeting_agent(task)
    bucket = _delegations.get()
    if bucket is not None:
        bucket.append(run)
    return run["response"]


# Step 4+: add delegate_to_news_agent / delegate_to_advisory_agent here.
TOOLS = [delegate_to_meeting_agent]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

SYSTEM_PROMPT = """당신은 K전자 슈퍼에이전트(K Electronics Super Agent)다.
그룹 슈퍼에이전트로부터 A2A로 받은 검토 요청을 수행하기 위해, 전문 에이전트에게 업무를 자연어로 위임한다.

규칙:
- 직접 분석하거나 답을 지어내지 않는다. 반드시 등록된 위임 Tool을 호출해 전문 에이전트에게 업무를 맡긴다.
- 위임 문장(task)은 전문 에이전트가 바로 수행할 수 있도록 구체적으로 쓴다. 대상 기업명, 찾아야 할 것(결정된 기준, 미완료 Action Item 등)을 포함한다.
- 위임 문장은 한국어로, 2~3문장 이내로 간결하게 작성한다. 전문 에이전트의 담당 범위를 벗어난 요구(외부 뉴스, 재무 분석 등)는 넣지 않는다."""


def _call_model(state: MessagesState) -> dict:
    model = get_chat_model().bind_tools(TOOLS)
    response = model.invoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
    return {"messages": [response]}


def _run_tools(state: MessagesState) -> dict:
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    return {"messages": execute_tool_calls(last, TOOLS_BY_NAME)}


def _route(state: MessagesState) -> Literal["tools", "__end__"]:
    last = state["messages"][-1]
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else END


@lru_cache(maxsize=1)
def build_k_super() -> CompiledStateGraph:
    graph = StateGraph(MessagesState)
    graph.add_node("model", _call_model)
    graph.add_node("tools", _run_tools)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", _route, {"tools": "tools", END: END})
    # Step 6 (synthesis): replace with graph.add_edge("tools", "model").
    graph.add_edge("tools", END)
    return graph.compile()


class NoDelegationError(RuntimeError):
    """The model answered without delegating to any specialist agent."""


def run_k_super(request: str) -> list[AgentRun]:
    """Runs K Super on an A2A request and returns the specialist traces."""
    token = _delegations.set([])
    try:
        build_k_super().invoke(
            {"messages": [HumanMessage(request)]},
            config={"recursion_limit": RECURSION_LIMIT},
        )
        delegations = _delegations.get() or []
    finally:
        _delegations.reset(token)
    if not delegations:
        raise NoDelegationError("K전자 슈퍼에이전트가 전문 에이전트에게 업무를 위임하지 않았습니다.")
    return delegations
