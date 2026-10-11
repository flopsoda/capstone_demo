"""K Electronics Super Agent — delegates work to specialist agents in natural language.

    A2A request → model → delegate_to_*(task) → model → delegate_to_*(task) → … → model → END
                               │                              │                       │
                               └─ runs a specialist agent     └─ runs another one     └─ final answer (no tool call)

Each specialist agent is exposed to the model as one delegation Tool
(app/capabilities/tools/). The tool's argument is the natural-language task;
its implementation runs the specialist's own graph and returns only that
agent's final answer, so the specialist's internal tool calls never enter
K Super's history.

Which Skills and Tools this agent gets is decided by the Capability Registry
(app/capabilities/registry.py), looked up by AGENT_ID. Every model turn
decides the next action from the request and the results received so far:
another delegation (tool call) or, when nothing more is needed, the final
answer (plain AI message). The run ends on that final answer (_route → END);
there is no separate termination rule and the registry's tool list is not a
checklist that has to be exhausted.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..capabilities import skills_for, tools_for
from ..events import AgentError, emit
from ..llm import get_chat_model
from .common import RECURSION_LIMIT, compose_system_prompt, final_response, tool_call_names

AGENT_ID = "k_super"

IDENTITY = """당신은 K전자 슈퍼에이전트(K Electronics Super Agent)다.
그룹 슈퍼에이전트로부터 A2A로 받은 검토 요청을 수행하기 위해, 전문 에이전트에게 업무를 자연어로 위임하고
그 결과를 종합해 K전자 차원의 최종 검토 결과를 그룹 슈퍼에이전트에게 회신한다.

출력 규칙:
- 최종 응답은 한국어로 작성한다. 고유명사, 기술명, Time-to-Market 같은 용어는 원문 표기를 유지해도 된다.
- 출력은 채팅창에 일반 텍스트로 표시된다. 마크다운 문법(#, **, 표, 코드블록)은 사용하지 말고,
  줄바꿈과 "- " 목록만 사용한다."""

# Allowed capability subset, from the registry.
SYSTEM_PROMPT = compose_system_prompt(IDENTITY, skills_for(AGENT_ID))
TOOLS = tools_for(AGENT_ID)
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def _call_model(state: MessagesState) -> dict:
    model = get_chat_model().bind_tools(TOOLS)
    response = model.invoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
    return {"messages": [response]}


def _run_tools(state: MessagesState) -> dict:
    """Runs the delegation tool(s) the model chose, one after another.
    Delegation events are emitted by the tools themselves (delegation_start/end),
    not as generic tool events, because to the UI a delegation is an
    agent-to-agent handoff."""
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    out = []
    for call in last.tool_calls:
        tool_ = TOOLS_BY_NAME.get(call["name"])
        if tool_ is None:
            raise AgentError(AGENT_ID, f"알 수 없는 위임 Tool: {call['name']}")
        result = tool_.invoke(call["args"])
        out.append(ToolMessage(content=str(result), tool_call_id=call["id"], name=call["name"]))
    return {"messages": out}


def _route(state: MessagesState) -> Literal["tools", "__end__"]:
    """A tool call means another delegation; a plain answer is the final response and ends the run."""
    last = state["messages"][-1]
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else END


@lru_cache(maxsize=1)
def build_k_super() -> CompiledStateGraph:
    graph = StateGraph(MessagesState)
    graph.add_node("model", _call_model)
    graph.add_node("tools", _run_tools)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", _route, {"tools": "tools", END: END})
    # After every delegation result the model decides again: delegate more, or answer.
    graph.add_edge("tools", "model")
    return graph.compile()


def run_k_super(request: str) -> str:
    """Runs K Super on an A2A request and returns its final answer (the
    subsidiary-level review that goes back to the requester). Lifecycle is
    reported through emit().

    Which specialists are consulted, and how many, is the model's decision per
    request. The one check kept is grounding: an answer produced without any
    delegation would be K Super's own invention, which the Skill forbids.
    """
    emit({"type": "agent_start", "agent": AGENT_ID, "request": request})
    try:
        state = build_k_super().invoke(
            {"messages": [HumanMessage(request)]},
            config={"recursion_limit": RECURSION_LIMIT},
        )
    except AgentError:
        raise  # a specialist already attributed the failure
    except Exception as e:
        raise AgentError(AGENT_ID, f"{type(e).__name__}: {e}") from e
    messages = state["messages"]
    if not tool_call_names(messages):
        raise AgentError(AGENT_ID, "K전자 슈퍼에이전트가 전문 에이전트에게 업무를 위임하지 않고 응답했습니다.")
    response = final_response(messages)
    if not response:
        raise AgentError(AGENT_ID, "K전자 슈퍼에이전트가 최종 검토 결과를 생성하지 않았습니다.")
    emit({"type": "agent_end", "agent": AGENT_ID, "response": response})
    return response
