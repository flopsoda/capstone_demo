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

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import message_chunk_to_message
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from .capabilities import Skill
from .events import AgentError, emit
from .llm import get_chat_model

# Upper bound on LangGraph super-steps per agent invocation (model ↔ tools loop).
RECURSION_LIMIT = 10

Invocation = Literal["delegation", "a2a", "direct"]

# A direct chat's earlier turns arrive inside the request, before the current one ("[이전 대화] … [현재 요청] …",
# the gateway's format: navitech agents/remote.py build_request). Delegations and A2A requests never carry them.
HISTORY_CONTEXT = (
    "요청 앞에 [이전 대화]가 붙어 오면 사용자와 앞서 나눈 대화다. 대상(기업 · 안건 · 업무 · 담당자 · 조건)을 이해하는 데만 쓰고 [현재 요청]만 수행한다. 이전 대화에서 이미 끝낸 메일 발송 · 저장 · 등록은 다시 하지 않는다 (조회는 필요하면 다시 한다)."
)

# Specialist agents: "delegation" (a Super Agent handed over a task) or "direct".
INVOCATION_CONTEXT: dict[Invocation, str] = {
    "delegation": (
        "호출 맥락: 이 요청은 슈퍼에이전트가 위임한 업무다. 응답은 슈퍼에이전트에게 돌아가는 보고이므로 "
        "반드시 등록된 Workflow Tool의 결과에 근거해 작성한다. 인사말이나 되묻기 없이 결과만 보고한다."
    ),
    "direct": (
        "호출 맥락: 이 요청은 사용자와의 직접 대화다. 인사, 일반적인 개념 설명, 네 역할에 대한 질문에는 "
        "Tool 없이 자연스럽게 답한다. 실제 데이터나 기록에 근거해야 하는 업무 요청이면 등록된 Workflow Tool을 "
        "사용한 뒤 그 결과에 근거해 답한다. " + HISTORY_CONTEXT
    ),
}

# Super agents: "a2a" (another agent system's Super Agent sent a request) or "direct".
SUPER_INVOCATION_CONTEXT: dict[Invocation, str] = {
    "a2a": (
        "호출 맥락: 이 요청은 다른 Agent System의 슈퍼에이전트가 A2A로 보낸 업무 요청이다. 응답은 그 시스템에 "
        "돌아가는 회신이므로 반드시 등록된 Tool(전문 에이전트 위임, 다른 Agent System에 대한 A2A 요청)로 받은 결과에 근거해 작성한다. "
        "인사말이나 되묻기 없이 결과만 회신한다."
    ),
    "direct": (
        "호출 맥락: 이 요청은 사용자와의 직접 대화다. 인사, 일반적인 개념 설명, 네 역할에 대한 질문에는 "
        "Tool 없이 자연스럽게 답한다. 데이터나 기록에 근거해야 하는 검토 요청이면 등록된 Tool로 담당 에이전트(전문 에이전트, "
        "또는 다른 Agent System의 슈퍼에이전트)에게 맡긴 뒤 그 결과에 근거해 답한다. " + HISTORY_CONTEXT
    ),
}


def compose_system_prompt(identity: str, skills: Sequence[Skill]) -> str:
    """System context = the agent's own identity/role text + its allowed Skills (playbooks)."""
    parts = [identity.strip()]
    for skill in skills:
        parts.append(f"업무 지침 — {skill.name}:\n{skill.instructions.strip()}")
    return "\n\n".join(parts)


MAX_ARGUMENT_ERRORS = 10  # argument problems quoted back to the model


def argument_error(name: str, error: ValidationError) -> dict[str, str]:
    """Arguments that do not fit the Tool's schema, as a result the model can correct and call again
    (a model that names a field differently must not end the whole run)."""
    problems = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors()[:MAX_ARGUMENT_ERRORS]]
    return {"error": f"{name}의 인자가 Tool 정의와 맞지 않아 실행하지 않았습니다 — " + "; ".join(problems)
                     + ". Tool 설명에 적힌 필드 이름과 형식 그대로 다시 호출하세요."}


def execute_tool_calls(agent: str, message: AIMessage, tools: dict[str, BaseTool]) -> list[ToolMessage]:
    """Runs every tool call the model made, emitting tool_start / tool_end around each."""
    out: list[ToolMessage] = []
    for call in message.tool_calls:
        emit({"type": "tool_start", "agent": agent, "tool": call["name"], "args": dict(call["args"])})
        tool = tools.get(call["name"])
        if tool is None:
            content: Any = {"error": f"unknown tool: {call['name']}"}
        else:
            try:
                content = tool.invoke(call["args"])
            except ValidationError as e:  # the arguments did not fit the Tool's schema
                content = argument_error(call["name"], e)
        emit({"type": "tool_end", "agent": agent, "tool": call["name"], "result": content})
        out.append(
            ToolMessage(
                content=content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
                tool_call_id=call["id"],
                name=call["name"],
            )
        )
    return out


def _text_blocks(content: str | list) -> list[str]:
    if isinstance(content, str):
        return [content]
    out: list[str] = []
    for block in content:
        if isinstance(block, str):
            out.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            out.append(str(block.get("text", "")))
    return out


def message_text(message: BaseMessage) -> str:
    """Plain text of a message (models may return a list of content blocks)."""
    return "\n".join(p for p in _text_blocks(message.content) if p).strip()


def invoke_model(agent: str, model: Any, messages: Sequence[BaseMessage]) -> AIMessage:
    """One model turn. The model's text is streamed out as `agent_token` events while
    it is generated (so the UI can show the answer growing) and the merged message is
    returned to the graph exactly as `invoke` would have — tool calls included.
    A model that yields a complete AIMessage instead of chunks (test stubs) is used as is.
    """
    merged: AIMessageChunk | None = None
    for chunk in model.stream(messages):
        if not isinstance(chunk, AIMessageChunk):
            return chunk
        text = "".join(_text_blocks(chunk.content))
        if text:
            emit({"type": "agent_token", "agent": agent, "text": text})
        merged = chunk if merged is None else merged + chunk
    if merged is None:
        return AIMessage(content="")
    message = message_chunk_to_message(merged)
    assert isinstance(message, AIMessage)
    return message


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


def _invocation(config: RunnableConfig, default: Invocation = "delegation") -> Invocation:
    return config.get("configurable", {}).get("invocation", default)


def build_specialist_graph(spec: Specialist) -> CompiledStateGraph:
    """model ⇄ tools loop over MessagesState; the model ends the run by answering without a tool call."""
    tools = list(spec.tools)
    tools_by_name = spec.tools_by_name

    def call_model(state: MessagesState, config: RunnableConfig) -> dict:
        system = f"{spec.system_prompt}\n\n{INVOCATION_CONTEXT[_invocation(config)]}"
        model = get_chat_model().bind_tools(tools)
        response = invoke_model(spec.agent_id, model, [SystemMessage(system), *state["messages"]])
        return {"messages": [response]}

    def run_tools(state: MessagesState) -> dict:
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        return {"messages": execute_tool_calls(spec.agent_id, last, tools_by_name)}

    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_node("tools", run_tools)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", route_on_tool_calls, {"tools": "tools", END: END})
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


# ---------------------------------------------------------------------------
# Super agent core
# ---------------------------------------------------------------------------
#
#     request → model → delegate_to_*(task) → model → … → model → END
#
# A Super Agent's Tools are handoffs to other agents (delegation Tools, A2A
# client Tools). The Tools report the handoff themselves (delegation_start/end,
# a2a_start/end) and return only the other agent's final answer, so this core
# emits no tool_start/tool_end. Every model turn decides the next action from
# the request and the results received so far: another handoff (tool call) or,
# when nothing more is needed, the final answer (plain AI message). There is no
# separate termination rule and the registry's tool list is not a checklist.


@dataclass(frozen=True)
class SuperAgent:
    """What distinguishes one super agent from another."""

    agent_id: str
    display_name: str  # Korean name used in error messages
    system_prompt: str  # identity + Skills from the registry
    tools: tuple[BaseTool, ...]  # handoff Tools from the registry

    @property
    def tools_by_name(self) -> dict[str, BaseTool]:
        return {t.name: t for t in self.tools}


def route_on_tool_calls(state: MessagesState) -> Literal["tools", "__end__"]:
    """A tool call means another handoff; a plain answer is the final response and ends the run."""
    last = state["messages"][-1]
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else END


def build_super_graph(spec: SuperAgent) -> CompiledStateGraph:
    """model ⇄ tools loop over MessagesState; after every handoff result the model decides again."""
    tools = list(spec.tools)
    tools_by_name = spec.tools_by_name

    def call_model(state: MessagesState, config: RunnableConfig) -> dict:
        system = f"{spec.system_prompt}\n\n{SUPER_INVOCATION_CONTEXT[_invocation(config, 'a2a')]}"
        model = get_chat_model().bind_tools(tools)
        response = invoke_model(spec.agent_id, model, [SystemMessage(system), *state["messages"]])
        return {"messages": [response]}

    def run_tools(state: MessagesState) -> dict:
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        out = []
        for call in last.tool_calls:
            tool_ = tools_by_name.get(call["name"])
            if tool_ is None:
                raise AgentError(spec.agent_id, f"알 수 없는 위임 Tool: {call['name']}")
            result = tool_.invoke(call["args"])
            out.append(ToolMessage(content=str(result), tool_call_id=call["id"], name=call["name"]))
        return {"messages": out}

    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_node("tools", run_tools)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", route_on_tool_calls, {"tools": "tools", END: END})
    graph.add_edge("tools", "model")
    return graph.compile()


def run_super(spec: SuperAgent, graph: CompiledStateGraph, request: str, *, invocation: Invocation) -> str:
    """Handles one request and returns the final answer.

    In the a2a context an answer produced without any handoff would be the
    Super Agent's own invention, which its Skill forbids: AgentError. In the
    direct context (a user chatting) the model may answer without a Tool.
    """
    emit({"type": "agent_start", "agent": spec.agent_id, "request": request})
    try:
        state = graph.invoke(
            {"messages": [HumanMessage(request)]},
            config={"recursion_limit": RECURSION_LIMIT, "configurable": {"invocation": invocation}},
        )
    except AgentError:
        raise  # a downstream agent already attributed the failure
    except Exception as e:
        raise AgentError(spec.agent_id, f"{type(e).__name__}: {e}") from e
    messages = state["messages"]
    if invocation == "a2a" and not tool_call_names(messages):
        raise AgentError(spec.agent_id, f"{spec.display_name}가 어떤 에이전트에게도 업무를 맡기지 않고 응답했습니다.")
    response = final_response(messages)
    if not response:
        raise AgentError(spec.agent_id, f"{spec.display_name}가 최종 검토 결과를 생성하지 않았습니다.")
    emit({"type": "agent_end", "agent": spec.agent_id, "response": response})
    return response
