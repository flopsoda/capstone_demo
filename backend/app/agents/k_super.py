"""K Electronics Super Agent — delegates work to specialist agents in natural language.

    A2A request → model → delegate_to_*(task) → model → delegate_to_*(task) → … → END
                               │                              │
                               └─ runs the Meeting Agent      └─ runs the News Agent

Each specialist agent is exposed to the model as one delegation Tool
(app/capabilities/tools/). The tool's argument is the natural-language task;
its implementation runs the specialist's own graph and returns only that
agent's final answer, so the specialist's internal tool calls never enter
K Super's history.

Which Skills and Tools this agent gets is decided by the Capability Registry
(app/capabilities/registry.py), looked up by AGENT_ID. The model decides the
order of delegations and writes each task; after every delegation result it
decides again (tools → model loop).

Step 4 scope: the run ends as soon as every registered specialist has
reported (see _after_tools). Synthesis of the results (Step 6) is not done
yet; when it is, that boundary becomes a final model turn instead of END.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..capabilities import skills_for, tools_for
from ..events import AgentError, emit
from ..llm import get_chat_model
from .common import RECURSION_LIMIT, compose_system_prompt, final_response, tool_call_names

AGENT_ID = "k_super"

IDENTITY = """당신은 K전자 슈퍼에이전트(K Electronics Super Agent)다.
그룹 슈퍼에이전트로부터 A2A로 받은 검토 요청을 수행하기 위해, 전문 에이전트에게 업무를 자연어로 위임한다."""

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


def pending_delegations(messages: list[BaseMessage]) -> list[str]:
    """Delegation tools this agent has not called yet, in registry order."""
    used = set(tool_call_names(messages))
    return [name for name in TOOLS_BY_NAME if name not in used]


def _route(state: MessagesState) -> Literal["tools", "__end__"]:
    last = state["messages"][-1]
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else END


def _after_tools(state: MessagesState) -> Literal["model", "__end__"]:
    """Current real scope ends once every specialist has reported; otherwise the
    model looks at the result it just received and decides the next delegation."""
    return END if not pending_delegations(state["messages"]) else "model"


@lru_cache(maxsize=1)
def build_k_super() -> CompiledStateGraph:
    graph = StateGraph(MessagesState)
    graph.add_node("model", _call_model)
    graph.add_node("tools", _run_tools)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", _route, {"tools": "tools", END: END})
    # Step 6 (synthesis): replace with graph.add_edge("tools", "model").
    graph.add_conditional_edges("tools", _after_tools, {"model": "model", END: END})
    return graph.compile()


def run_k_super(request: str) -> str:
    """Runs K Super on an A2A request. Lifecycle is reported through emit();
    returns K Super's own closing text (empty while the run ends right after
    the last delegation)."""
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
        raise AgentError(AGENT_ID, "K전자 슈퍼에이전트가 전문 에이전트에게 업무를 위임하지 않았습니다.")
    pending = pending_delegations(messages)
    if pending:
        raise AgentError(
            AGENT_ID,
            f"K전자 슈퍼에이전트가 일부 전문 에이전트({', '.join(pending)})에게 위임하지 않고 종료했습니다.",
        )
    response = final_response(messages)
    emit({"type": "agent_end", "agent": AGENT_ID, "response": response})
    return response
