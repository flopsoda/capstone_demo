"""Meeting Agent — a LangGraph agent whose state is its own message history.

    request → model → (tool call?) → run_meeting_context_retrieval → model → answer

The model decides, via tool calling, whether to run the Workflow Tool.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..llm import get_chat_model
from ..workflows.meeting_context_retrieval import run_meeting_context_retrieval
from .common import RECURSION_LIMIT, AgentRun, collect_tool_calls, execute_tool_calls, final_response

AGENT_ID = "meeting"

TOOLS = [run_meeting_context_retrieval]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

SYSTEM_PROMPT = """당신은 K전자의 미팅 에이전트(Meeting Agent)다.
역할: 사내 회의 기록을 바탕으로 과거 회의에서 정해진 기준·결정사항과 아직 완료되지 않은 Action Item을 찾아 정리한다.

규칙:
- 회의 기록은 반드시 등록된 Workflow Tool(run_meeting_context_retrieval)로 조회한다. 기억이나 추측으로 회의 내용을 만들어내지 않는다.
- Tool 결과에 없는 내용은 답하지 않는다. 관련 회의가 없으면 그렇게 말한다.
- 최종 응답은 한국어로 작성한다. 고유명사, 기술명, Workflow 이름, Action Item, Time-to-Market 같은 용어는 원문 표기를 유지해도 된다.
- 최종 응답 형식 (순서 고정):
  1) 회의 식별 (날짜 · 회의명)
  2) 결정된 기준 — 발언자별로 한 줄씩
  3) 미완료 Action Item — 목록
  불필요한 서론과 결론은 생략하고 간결하게 작성한다.
- 출력은 채팅창에 일반 텍스트로 표시된다. 마크다운 문법(#, **, 표, 코드블록)은 사용하지 말고,
  줄바꿈과 "- " 목록만 사용한다. 기록에 없는 항목(기한, 참석자 등)은 언급하지 않는다."""


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
def build_meeting_agent() -> CompiledStateGraph:
    graph = StateGraph(MessagesState)
    graph.add_node("model", _call_model)
    graph.add_node("tools", _run_tools)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", _route, {"tools": "tools", END: END})
    graph.add_edge("tools", "model")
    return graph.compile()


def run_meeting_agent(task: str) -> AgentRun:
    """Handles one natural-language task and returns the trace + final answer."""
    state = build_meeting_agent().invoke(
        {"messages": [HumanMessage(task)]},
        config={"recursion_limit": RECURSION_LIMIT},
    )
    messages = state["messages"]
    return {
        "agent": AGENT_ID,
        "request": task,
        "tool_calls": collect_tool_calls(messages),
        "response": final_response(messages),
    }
