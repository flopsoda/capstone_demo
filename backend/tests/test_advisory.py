"""Advisory Agent (K Electronics and Group) on the shared specialist core, offline.

A scripted chat model drives the real agent → run_mna_review → workflow chain. These pin
the platform behaviour the M&A workflow extension must keep: lifecycle events reach the
stream while the agent is still working, a failure keeps what already happened, a Tool
error goes back to the model to correct, and the agent's answer is the model's own.
"""

import json
import threading

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk

from app.main import app
from app.runtime import agent_core
from app.runtime.events import new_run, stream_run
from app.systems.group.agents import advisory as group_advisory
from app.systems.k_electronics.agents import advisory as k_advisory

# (system, agent module, a company in that system's data)
SYSTEMS = [
    pytest.param("k_electronics", k_advisory, "뉴로엣지", id="k_electronics"),
    pytest.param("group", group_advisory, "브라이트셀", id="group"),
]


class _Model:
    """Turns: AIMessage (returned whole: tool calls), list[str] (answer streamed in chunks),
    Exception (the model fails). An answer turn first waits for `gate` when one is given."""

    def __init__(self, turns, gate: threading.Event | None = None):
        self.turns = list(turns)
        self.gate = gate
        self.seen: list[list] = []

    def bind_tools(self, _tools):
        return self

    def stream(self, messages):
        self.seen.append(list(messages))
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        if isinstance(turn, AIMessage):
            yield turn
            return
        if self.gate is not None and not self.gate.wait(timeout=5):
            raise AssertionError("tool_end did not reach the stream before the answer was written")
        for piece in turn:
            yield AIMessageChunk(content=piece)


def _call(idx, **args):
    return AIMessage(content="", tool_calls=[{"name": "run_mna_review", "args": args, "id": f"c{idx}"}])


def _stream(system, fn, on_event=None):
    events = []
    for ev in stream_run(fn, run=new_run(system=system, root_agent="advisory"), hub=None):
        events.append(ev)
        if on_event:
            on_event(ev)
    return events


@pytest.mark.parametrize("system,agent,company", SYSTEMS)
def test_lifecycle_streams_while_the_agent_is_still_working(monkeypatch, system, agent, company):
    gate = threading.Event()
    model = _Model([_call(1, query="인수 검토", company=company, persona="CFO"), ["권고: ", "조건부 진행"]], gate)
    monkeypatch.setattr(agent_core, "get_chat_model", lambda: model)

    def on_event(ev):
        if ev["type"] == "tool_end":
            gate.set()  # the answer turn may only start once tool_end has been streamed

    events = _stream(system, lambda: agent.run_advisory_agent("인수 검토", invocation="direct"), on_event)

    assert [e["type"] for e in events] == [
        "agent_start", "tool_start", "tool_end", "agent_token", "agent_token", "agent_end", "done",
    ], events
    assert [e["text"] for e in events if e["type"] == "agent_token"] == ["권고: ", "조건부 진행"]
    assert events[-2]["response"] == "권고: 조건부 진행"  # the model's own answer, not rewritten
    assert all(e["system"] == system for e in events)


@pytest.mark.parametrize("system,agent,company", SYSTEMS)
def test_failure_after_the_tool_keeps_the_events_already_streamed(monkeypatch, system, agent, company):
    model = _Model([_call(1, query="인수 검토", company=company, persona="CEO"), ConnectionError("LLM down")])
    monkeypatch.setattr(agent_core, "get_chat_model", lambda: model)

    events = _stream(system, lambda: agent.run_advisory_agent("인수 검토", invocation="direct"))

    assert [e["type"] for e in events] == ["agent_start", "tool_start", "tool_end", "error"]
    assert events[-1]["agent"] == "advisory" and "LLM down" in events[-1]["message"]


@pytest.mark.parametrize("system,agent,company", SYSTEMS)
def test_tool_error_goes_back_to_the_model_to_correct(monkeypatch, system, agent, company):
    model = _Model([
        _call(1, query="2.2조면?", company=company, persona="ALL", asking_trillion_krw=2.2),  # not supported
        _call(2, query="2.2조면?", company=company, persona="CFO", asking_trillion_krw=2.2),  # corrected
        ["CFO 관점 What-if 결과"],
    ])
    monkeypatch.setattr(agent_core, "get_chat_model", lambda: model)

    events = _stream(system, lambda: agent.run_advisory_agent("2.2조에 인수하면?", invocation="delegation"))

    results = [e["result"] for e in events if e["type"] == "tool_end"]
    assert "error" in results[0] and "ALL" in results[0]["error"]
    assert results[1]["mode"] == "what_if" and results[1]["persona"] == "CFO"
    assert "ALL" in model.seen[1][-1].content  # the model saw the Tool's error message
    assert events[-1]["type"] == "done" and events[-2]["response"] == "CFO 관점 What-if 결과"


@pytest.mark.parametrize("system,agent,company", SYSTEMS)
def test_executive_summary_and_what_if_reach_the_agent_and_the_ui(monkeypatch, system, agent, company):
    model = _Model([
        _call(1, query="종합 평가", company=company, persona="ALL"),
        _call(2, query="영업이익률 5%면?", company=company, persona="CTO", operating_margin=0.05),
        ["종합 평가와 What-if 결과"],
    ])
    monkeypatch.setattr(agent_core, "get_chat_model", lambda: model)

    events = _stream(system, lambda: agent.run_advisory_agent("검토", invocation="direct"))

    summary, what_if = [e["result"] for e in events if e["type"] == "tool_end"]
    assert summary["mode"] == "executive_summary" and set(summary["executive_reviews"]) == {"CEO", "CFO", "CTO"}
    assert what_if["mode"] == "what_if" and what_if["assumptions"]["operating_margin"] == 0.05
    # fields the frontend's single-perspective renderer reads are present on What-if results
    assert {"persona_label", "inputs", "assessments", "recommendation_label", "conditions"} <= set(what_if)
    assert events[-2] == {**events[-2], "type": "agent_end", "response": "종합 평가와 What-if 결과"}


def test_k_super_delegates_to_advisory_over_a2a(monkeypatch):
    model = _Model([
        AIMessage(content="", tool_calls=[{"name": "delegate_to_advisory_agent", "args": {"task": "뉴로엣지 인수 종합 검토"}, "id": "d1"}]),
        _call(2, query="인수 검토", company="뉴로엣지", persona="ALL"),
        ["경영자문 보고"],
        ["K전자 검토 결과"],
    ])
    monkeypatch.setattr(agent_core, "get_chat_model", lambda: model)

    with TestClient(app).stream("POST", "/api/a2a/run", json={"request": "뉴로엣지 검토", "trace_id": "tr-1", "caller": "group"}) as res:
        events = [json.loads(line) for line in res.iter_lines() if line.strip()]

    assert [e["type"] for e in events] == [
        "agent_start", "delegation_start", "agent_start", "tool_start", "tool_end",
        "agent_token", "agent_end", "delegation_end", "agent_token", "agent_end", "done",
    ]
    assert events[6]["agent"] == "advisory" and events[6]["response"] == "경영자문 보고"
    assert events[7]["response"] == "경영자문 보고"  # the delegator gets the specialist's answer as is
    assert events[-2]["agent"] == "k_super" and events[-2]["response"] == "K전자 검토 결과"
    assert all(e["trace_id"] == "tr-1" and e["origin"] == "a2a" for e in events)
