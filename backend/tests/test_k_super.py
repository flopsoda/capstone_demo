"""K Super graph control flow without an LLM.

Every model turn decides the next action: a tool call routes to the
delegation tools and back to the model; a plain answer ends the run. There is
no rule that every registered specialist must be consulted.
"""

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END

from app.agents import common, k_super
from app.agents.common import build_super_graph, route_on_tool_calls
from app.agents.k_super import SPEC, TOOLS_BY_NAME, build_k_super, run_k_super
from app.events import AgentError, collect_events


def _tool_call(name: str, idx: int) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": {"task": "t"}, "id": f"c{idx}"}])


def test_registry_gives_k_super_one_delegation_tool_per_specialist():
    assert list(TOOLS_BY_NAME) == ["delegate_to_meeting_agent", "delegate_to_news_agent", "delegate_to_advisory_agent"]


def test_route_delegates_on_tool_call_and_ends_on_plain_answer():
    assert route_on_tool_calls({"messages": [HumanMessage("req"), _tool_call("delegate_to_meeting_agent", 1)]}) == "tools"
    assert route_on_tool_calls({"messages": [HumanMessage("req"), AIMessage("최종 검토 결과")]}) == END


def test_tools_always_return_to_model():
    edges = {(e.source, e.target) for e in build_k_super().get_graph().edges}
    assert ("tools", "model") in edges
    assert ("tools", END) not in edges


# --- run_k_super with a scripted model and stubbed delegation tools ---------


class _ScriptedModel:
    """Returns the scripted AI messages one per model turn."""

    def __init__(self, turns: list[AIMessage]):
        self.turns = list(turns)

    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        return self.turns.pop(0)


class _StubDelegation:
    def __init__(self, response: str, name: str = "stub"):
        self.name = name
        self.response = response
        self.calls: list[dict] = []

    def invoke(self, args):
        self.calls.append(args)
        return self.response


@pytest.fixture
def scripted(monkeypatch):
    def install(turns: list[AIMessage], stubs: dict[str, _StubDelegation]):
        model = _ScriptedModel(turns)  # one instance: each model turn consumes the next scripted message
        monkeypatch.setattr(common, "get_chat_model", lambda: model)
        for name, stub in stubs.items():
            stub.name = name
        graph = build_super_graph(replace(SPEC, tools=tuple(stubs.values())))
        monkeypatch.setattr(k_super, "build_k_super", lambda: graph)

    return install


def test_run_ends_on_final_answer_without_consulting_every_specialist(scripted):
    meeting = _StubDelegation("회의 결과")
    scripted(
        [_tool_call("delegate_to_meeting_agent", 1), AIMessage("회의 결정사항 정리: ...")],
        {"delegate_to_meeting_agent": meeting, "delegate_to_news_agent": _StubDelegation("x")},
    )
    response, events = collect_events(lambda: run_k_super("지난 회의 결정사항만"))
    assert response == "회의 결정사항 정리: ..."
    assert meeting.calls == [{"task": "t"}]
    assert [e["type"] for e in events] == ["agent_start", "agent_end"]
    assert events[-1] == {"type": "agent_end", "agent": "k_super", "response": response}


def test_model_keeps_delegating_until_it_answers(scripted):
    stubs = {name: _StubDelegation(f"{name} 결과") for name in TOOLS_BY_NAME}
    scripted(
        [
            _tool_call("delegate_to_meeting_agent", 1),
            _tool_call("delegate_to_news_agent", 2),
            _tool_call("delegate_to_advisory_agent", 3),
            AIMessage("종합 결과"),
        ],
        stubs,
    )
    response, _ = collect_events(lambda: run_k_super("전체 검토"))
    assert response == "종합 결과"
    assert all(len(s.calls) == 1 for s in stubs.values())


def test_answer_without_any_delegation_is_an_error(scripted):
    scripted([AIMessage("제가 보기엔 진행해도 됩니다.")], {})
    with pytest.raises(AgentError, match="위임하지 않고"):
        collect_events(lambda: run_k_super("검토해줘"))


def test_empty_final_answer_is_an_error(scripted):
    scripted(
        [_tool_call("delegate_to_meeting_agent", 1), AIMessage("")],
        {"delegate_to_meeting_agent": _StubDelegation("회의 결과")},
    )
    with pytest.raises(AgentError, match="최종 검토 결과를 생성하지"):
        collect_events(lambda: run_k_super("검토해줘"))


def test_tool_message_shape_after_delegation(scripted):
    scripted(
        [_tool_call("delegate_to_meeting_agent", 1), AIMessage("끝")],
        {"delegate_to_meeting_agent": _StubDelegation("회의 결과")},
    )
    state = k_super.build_k_super().invoke({"messages": [HumanMessage("req")]})  # the patched graph
    tool_msg = state["messages"][2]
    assert isinstance(tool_msg, ToolMessage) and tool_msg.content == "회의 결과" and tool_msg.tool_call_id == "c1"
    assert isinstance(state["messages"][-1], AIMessage) and not state["messages"][-1].tool_calls
