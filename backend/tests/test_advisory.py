"""Advisory Agent (K Electronics and Group) on the shared specialist core, offline.

A scripted chat model drives the real agent → run_mna_review → workflow chain. These pin
the platform behaviour the M&A workflow extension must keep: lifecycle events reach the
stream while the agent is still working, a failure keeps what already happened, a Tool
error goes back to the model to correct, and the agent's answer is the model's own.
"""

import json
import threading

import httpx
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
    model = _Model([_call(1, query="인수 검토", company=company), ["권고: ", "조건부 진행"]], gate)
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
    model = _Model([_call(1, query="인수 검토", company=company), ConnectionError("LLM down")])
    monkeypatch.setattr(agent_core, "get_chat_model", lambda: model)

    events = _stream(system, lambda: agent.run_advisory_agent("인수 검토", invocation="direct"))

    assert [e["type"] for e in events] == ["agent_start", "tool_start", "tool_end", "error"]
    assert events[-1]["agent"] == "advisory" and "LLM down" in events[-1]["message"]


@pytest.mark.parametrize("system,agent,company", SYSTEMS)
def test_a_timed_out_model_turn_is_asked_again_like_an_empty_one(monkeypatch, system, agent, company):
    # The provider did not finish the turn within LLM_TIMEOUT_SECONDS: the run goes on as after an empty turn.
    model = _Model([_call(1, query="인수 검토", company=company), httpx.ReadTimeout("timed out"), ["권고: ", "조건부 진행"]])
    monkeypatch.setattr(agent_core, "get_chat_model", lambda: model)

    events = _stream(system, lambda: agent.run_advisory_agent("인수 검토", invocation="direct"))

    assert [e["type"] for e in events] == [
        "agent_start", "tool_start", "tool_end", "agent_token", "agent_token", "agent_end", "done",
    ], events
    assert events[-2]["response"] == "권고: 조건부 진행"
    assert model.seen[-1][-1].content == agent_core.EMPTY_TURN_NUDGE  # asked again with the empty-turn nudge


@pytest.mark.parametrize("system,agent,company", SYSTEMS)
def test_tool_error_goes_back_to_the_model_to_correct(monkeypatch, system, agent, company):
    model = _Model([
        _call(1, query="최대 고객을 잃으면?", company=company, scenarios=[{"name": "이탈", "largest_customer_revenue_loss": 1.5}]),  # not a share
        _call(2, query="최대 고객을 잃으면?", company=company, scenarios=[{"name": "이탈", "largest_customer_revenue_loss": 1.0}]),  # corrected
        ["시나리오 결과"],
    ])
    monkeypatch.setattr(agent_core, "get_chat_model", lambda: model)

    events = _stream(system, lambda: agent.run_advisory_agent("최대 고객을 잃으면?", invocation="delegation"))

    results = [e["result"] for e in events if e["type"] == "tool_end"]
    assert "error" in results[0] and "0~1" in results[0]["error"]
    assert results[1]["scenarios"][0]["name"] == "이탈" and results[1]["analysis_id"] == "A1"  # the failed call was not counted
    assert "0~1" in model.seen[1][-1].content  # the model saw the Tool's error message
    assert events[-1]["type"] == "done" and events[-2]["response"] == "시나리오 결과"


@pytest.mark.parametrize("system,agent,company", SYSTEMS)
def test_prices_and_scenarios_are_computed_by_the_same_model(monkeypatch, system, agent, company):
    model = _Model([
        _call(1, query="평가", company=company),
        _call(2, query="가격과 고객 변화", company=company, price_trillion_krw=1.2,
              scenarios=[{"name": "최대 고객 절반 상실", "largest_customer_revenue_loss": 0.5}]),
        ["두 결과의 비교"],
    ])
    monkeypatch.setattr(agent_core, "get_chat_model", lambda: model)

    events = _stream(system, lambda: agent.run_advisory_agent("검토", invocation="direct"))

    base, changed = [e["result"] for e in events if e["type"] == "tool_end"]
    assert "recommendation" not in base and "recommendation" not in changed  # numbers only: the agent judges
    assert base["analysis_id"] == "A1" and changed["analysis_id"] == "A2"  # ids stay distinct within the run
    lines = [line for group in changed["figures"].values() for line in group]  # the model's compact view: "id — label: value"
    assert any(line.startswith("A2.deal.price — ") and line.endswith("1.2조 원") for line in lines) and changed["price"]["label"] == "검토 가정 가격"
    delta = next(line for line in lines if line.startswith("A2.S1.delta.equity_value — "))
    assert ": -" in delta  # losing revenue lowers the DCF value
    assert "tables" not in changed and changed["persona_lenses"]["CFO"]["groups"]  # the yearly tables stay in the ledger
    assert events[-2] == {**events[-2], "type": "agent_end", "response": "두 결과의 비교"}


def test_k_super_delegates_to_advisory_over_a2a(monkeypatch):
    model = _Model([
        AIMessage(content="", tool_calls=[{"name": "delegate_to_advisory_agent", "args": {"task": "뉴로엣지 인수 종합 검토"}, "id": "d1"}]),
        _call(2, query="인수 검토", company="뉴로엣지"),
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
