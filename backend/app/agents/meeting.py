"""Meeting Agent — a LangGraph agent whose state is its own message history.

    request → model → (tool call?) → run_meeting_context_retrieval → model → answer

The model decides, via tool calling, whether to run the Workflow Tool.
Which Skills and Tools it has is decided by the Capability Registry
(app/capabilities/registry.py), looked up by AGENT_ID.
Lifecycle events (agent_start, tool_start/end, agent_end) are emitted as the
graph actually runs; see app/events.py.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..capabilities import skills_for, tools_for
from ..events import AgentError, emit
from ..llm import get_chat_model
from .common import RECURSION_LIMIT, compose_system_prompt, execute_tool_calls, final_response, tool_call_names

AGENT_ID = "meeting"

IDENTITY = """당신은 K전자의 미팅 에이전트(Meeting Agent)다.
역할: 사내 회의 기록을 바탕으로 과거 회의에서 정해진 기준·결정사항과 아직 완료되지 않은 Action Item을 찾아 정리한다.

출력 규칙:
- 최종 응답은 한국어로 작성한다. 고유명사, 기술명, Workflow 이름, Action Item, Time-to-Market 같은 용어는 원문 표기를 유지해도 된다.
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
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    return {"messages": execute_tool_calls(AGENT_ID, last, TOOLS_BY_NAME)}


def _route(state: MessagesState) -> Literal["tools", "__end__"]:
    last = state["messages"][-1]
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else END


@lru_cache(maxsize=1)
def build_meeting_agent() -> CompiledStateGraph:
    graph = StateGraph(MessagesState)
    graph.add_node("model", _call_model)
    graph.add_node("tools", _run_tools)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", _route, {"tools": "tools", END: END})
    graph.add_edge("tools", "model")
    return graph.compile()


def run_meeting_agent(task: str) -> str:
    """Handles one natural-language task and returns the final answer.

    Raises AgentError("meeting", ...) if the model answered without running
    a Workflow Tool — an answer not backed by the meeting data is a failure.
    """
    emit({"type": "agent_start", "agent": AGENT_ID, "request": task})
    try:
        state = build_meeting_agent().invoke(
            {"messages": [HumanMessage(task)]},
            config={"recursion_limit": RECURSION_LIMIT},
        )
    except AgentError:
        raise
    except Exception as e:
        raise AgentError(AGENT_ID, f"{type(e).__name__}: {e}") from e
    messages = state["messages"]
    if not tool_call_names(messages):
        names = ", ".join(TOOLS_BY_NAME)
        raise AgentError(AGENT_ID, f"미팅 에이전트가 Workflow Tool({names})을 호출하지 않고 응답했습니다.")
    response = final_response(messages)
    if not response:
        raise AgentError(AGENT_ID, "미팅 에이전트가 최종 응답을 생성하지 않았습니다.")
    emit({"type": "agent_end", "agent": AGENT_ID, "response": response})
    return response
