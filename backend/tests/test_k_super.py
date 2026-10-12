"""K Super graph control flow without an LLM.

Every model turn decides the next action: a tool call routes to the
delegation tools and back to the model; a plain answer ends the run. There is
no rule that every registered specialist must be consulted.
"""

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END

from app.runtime import agent_core as common
from app.runtime.agent_core import build_super_graph, route_on_tool_calls
from app.runtime.events import AgentError, collect_events
from app.systems.k_electronics.agents import k_super
from app.systems.k_electronics.agents.k_super import SPEC, TOOLS_BY_NAME, build_k_super, run_k_super


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

    def stream(self, messages):
        yield self.invoke(messages)  # a whole message, not chunks: used as is


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
    with pytest.raises(AgentError, match="맡기지 않고"):
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


def test_direct_chat_may_answer_without_delegating(scripted):
    scripted([AIMessage("안녕하세요, K전자 슈퍼에이전트입니다.")], {})
    response, events = collect_events(lambda: run_k_super("안녕?", invocation="direct"))
    assert response == "안녕하세요, K전자 슈퍼에이전트입니다."
    assert [e["type"] for e in events] == ["agent_start", "agent_end"]


def test_model_turn_streams_text_as_tokens_and_reassembles_tool_calls():
    from langchain_core.messages import AIMessageChunk

    from app.runtime.agent_core import invoke_model

    class _StreamingModel:
        def stream(self, _messages):
            yield AIMessageChunk(content="회의 ", id="m1")
            yield AIMessageChunk(content="결정사항 정리")
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": "delegate_to_meeting_agent", "args": '{"task": "뉴로', "id": "c1", "index": 0}],
            )
            yield AIMessageChunk(content="", tool_call_chunks=[{"name": None, "args": '엣지 회의"}', "id": None, "index": 0}])

    message, events = collect_events(lambda: invoke_model("k_super", _StreamingModel(), []))
    assert [e["text"] for e in events] == ["회의 ", "결정사항 정리"]
    assert all(e["type"] == "agent_token" and e["agent"] == "k_super" for e in events)
    assert isinstance(message, AIMessage) and not isinstance(message, AIMessageChunk)
    assert message.content == "회의 결정사항 정리"
    assert message.tool_calls == [{"name": "delegate_to_meeting_agent", "args": {"task": "뉴로엣지 회의"}, "id": "c1", "type": "tool_call"}]


def test_model_turn_accepts_a_whole_message_from_a_non_streaming_model(scripted):
    scripted([AIMessage("안녕하세요")], {})
    response, events = collect_events(lambda: run_k_super("안녕?", invocation="direct"))
    assert response == "안녕하세요" and "agent_token" not in [e["type"] for e in events]
