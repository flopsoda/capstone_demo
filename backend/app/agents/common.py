"""Shared pieces for the message-history based agents.

Besides small helpers, this module holds the one specialist-agent core that
the Meeting / News / Advisory agents share:

    request → model → (tool call?) → Workflow Tool → model → answer

The model decides, via tool calling, whether to run a Workflow Tool. The
same graph serves two invocation contexts:

    delegation — a Super Agent handed the specialist a task. The answer goes
                 back to the Super Agent as a report, so it must be grounded
                 in a Workflow Tool result (otherwise AgentError).
    direct     — a user is chatting with the specialist. Small talk and
                 general questions get a plain answer; the model still calls
                 the Tool for work that needs real data.

The invocation context is passed per run (LangGraph config), added to the
system context so the model knows who it is talking to, and used for the
grounding check after the run. It is not part of the message state.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..capabilities import Skill
from ..events import AgentError, emit
from ..llm import get_chat_model

# Upper bound on LangGraph super-steps per agent invocation (model ↔ tools loop).
RECURSION_LIMIT = 10

Invocation = Literal["delegation", "direct"]

INVOCATION_CONTEXT: dict[Invocation, str] = {
    "delegation": (
        "호출 맥락: 이 요청은 슈퍼에이전트가 위임한 업무다. 응답은 슈퍼에이전트에게 돌아가는 보고이므로 "
        "반드시 등록된 Workflow Tool의 결과에 근거해 작성한다. 인사말이나 되묻기 없이 결과만 보고한다."
    ),
    "direct": (
        "호출 맥락: 이 요청은 사용자와의 직접 대화다. 인사, 일반적인 개념 설명, 네 역할에 대한 질문에는 "
        "Tool 없이 자연스럽게 답한다. 실제 데이터나 기록에 근거해야 하는 업무 요청이면 등록된 Workflow Tool을 "
        "사용한 뒤 그 결과에 근거해 답한다."
    ),
}


def compose_system_prompt(identity: str, skills: Sequence[Skill]) -> str:
    """System context = the agent's own identity/role text + its allowed Skills (playbooks)."""
    parts = [identity.strip()]
    for skill in skills:
        parts.append(f"업무 지침 — {skill.name}:\n{skill.instructions.strip()}")
    return "\n\n".join(parts)


def execute_tool_calls(agent: str, message: AIMessage, tools: dict[str, BaseTool]) -> list[ToolMessage]:
    """Runs every tool call the model made, emitting tool_start / tool_end around each."""
    out: list[ToolMessage] = []
    for call in message.tool_calls:
        emit({"type": "tool_start", "agent": agent, "tool": call["name"], "args": dict(call["args"])})
        tool = tools.get(call["name"])
        if tool is None:
            content: Any = {"error": f"unknown tool: {call['name']}"}
        else:
            content = tool.invoke(call["args"])
        emit({"type": "tool_end", "agent": agent, "tool": call["name"], "result": content})
        out.append(
            ToolMessage(
                content=content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
                tool_call_id=call["id"],
                name=call["name"],
            )
        )
    return out


def message_text(message: BaseMessage) -> str:
    """Plain text of a message (models may return a list of content blocks)."""
    content = message.content
    if isinstance(content, str):
        return content.strip()
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(p for p in parts if p).strip()


def tool_call_names(messages: list[BaseMessage]) -> list[str]:
    return [call["name"] for m in messages if isinstance(m, AIMessage) for call in m.tool_calls]


def final_response(messages: list[BaseMessage]) -> str:
    """Text of the last AI message that is not a tool call."""
    for m in reversed(messages):
        if isinstance(m, AIMessage) and not m.tool_calls:
            return message_text(m)
    return ""


# ---------------------------------------------------------------------------
# Specialist agent core
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Specialist:
    """What distinguishes one specialist agent from another."""

    agent_id: str
    display_name: str  # Korean name used in error messages
    system_prompt: str  # identity + Skills from the registry
    tools: tuple[BaseTool, ...]  # Tools from the registry

    @property
    def tools_by_name(self) -> dict[str, BaseTool]:
        return {t.name: t for t in self.tools}


def _invocation(config: RunnableConfig) -> Invocation:
    return config.get("configurable", {}).get("invocation", "delegation")


def build_specialist_graph(spec: Specialist) -> CompiledStateGraph:
    """model ⇄ tools loop over MessagesState; the model ends the run by answering without a tool call."""
    tools = list(spec.tools)
    tools_by_name = spec.tools_by_name

    def call_model(state: MessagesState, config: RunnableConfig) -> dict:
        system = f"{spec.system_prompt}\n\n{INVOCATION_CONTEXT[_invocation(config)]}"
        model = get_chat_model().bind_tools(tools)
        response = model.invoke([SystemMessage(system), *state["messages"]])
        return {"messages": [response]}

    def run_tools(state: MessagesState) -> dict:
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        return {"messages": execute_tool_calls(spec.agent_id, last, tools_by_name)}

    def route(state: MessagesState) -> Literal["tools", "__end__"]:
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else END

    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_node("tools", run_tools)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "model")
    return graph.compile()


def run_specialist(spec: Specialist, graph: CompiledStateGraph, task: str, *, invocation: Invocation) -> str:
    """Handles one natural-language task and returns the final answer.

    Lifecycle (agent_start, tool_start/end, agent_end) is reported through emit().
    In the delegation context an answer that did not use a Workflow Tool is an
    AgentError: a report not backed by data must not reach the Super Agent.
    """
    emit({"type": "agent_start", "agent": spec.agent_id, "request": task})
    try:
        state = graph.invoke(
            {"messages": [HumanMessage(task)]},
            config={"recursion_limit": RECURSION_LIMIT, "configurable": {"invocation": invocation}},
        )
    except AgentError:
        raise
    except Exception as e:
        raise AgentError(spec.agent_id, f"{type(e).__name__}: {e}") from e
    messages = state["messages"]
    if invocation == "delegation" and not tool_call_names(messages):
        names = ", ".join(spec.tools_by_name)
        raise AgentError(spec.agent_id, f"{spec.display_name}가 Workflow Tool({names})을 호출하지 않고 응답했습니다.")
    response = final_response(messages)
    if not response:
        raise AgentError(spec.agent_id, f"{spec.display_name}가 최종 응답을 생성하지 않았습니다.")
    emit({"type": "agent_end", "agent": spec.agent_id, "response": response})
    return response
