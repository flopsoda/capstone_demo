"""K Electronics app surfaces, offline: a scripted chat model drives the real
K Super → Meeting Agent → run_meeting_context_retrieval chain end to end."""

import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.main import app
from app.runtime import agent_core


class _ScriptedModel:
    def __init__(self, turns):
        self.turns = list(turns)

    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        return self.turns.pop(0)

    def stream(self, messages):
        yield self.invoke(messages)  # a whole message, not chunks: used as is


def _call(name, idx, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{idx}"}])


@pytest.fixture
def client(monkeypatch):
    def install(turns):
        model = _ScriptedModel(turns)
        monkeypatch.setattr(agent_core, "get_chat_model", lambda: model)
        return TestClient(app)

    return install


def _events(res):
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in res.iter_lines() if line.strip()]


def test_a2a_inbound_runs_k_super_with_the_callers_trace(client):
    c = client([
        _call("delegate_to_meeting_agent", 1, task="뉴로엣지 회의 결정사항"),  # K Super
        _call("run_meeting_context_retrieval", 2, query="뉴로엣지 인수", topics=["NeuroEdge"]),  # Meeting Agent
        AIMessage("2026-09-15 전략회의: CFO 2.5조 원 기준"),  # Meeting Agent answer
        AIMessage("K전자 검토 결과: 조건부 진행"),  # K Super final answer
    ])
    with c.stream("POST", "/api/a2a/run", json={"request": "뉴로엣지 검토", "trace_id": "tr-9", "caller": "group"}) as res:
        events = _events(res)
    assert [e["type"] for e in events] == [
        "agent_start", "delegation_start", "agent_start", "tool_start", "tool_end", "agent_end", "delegation_end", "agent_end", "done",
    ]
    assert events[-2] == {**events[-2], "agent": "k_super", "response": "K전자 검토 결과: 조건부 진행"}
    assert all((e["system"], e["origin"], e["trace_id"], e["caller"]) == ("k_electronics", "a2a", "tr-9", "group") for e in events)
    assert len({e["run_id"] for e in events}) == 1


def test_a2a_inbound_rejects_an_answer_without_delegation(client):
    c = client([AIMessage("제가 보기엔 진행해도 됩니다.")])
    with c.stream("POST", "/api/a2a/run", json={"request": "뉴로엣지 검토"}) as res:
        events = _events(res)
    assert events[-1]["type"] == "error" and events[-1]["agent"] == "k_super" and "맡기지 않고" in events[-1]["message"]


def test_direct_k_super_chat_may_answer_without_delegation(client):
    c = client([AIMessage("안녕하세요, K전자 슈퍼에이전트입니다.")])
    with c.stream("POST", "/api/agents/k-super/run", json={"request": "안녕?"}) as res:
        events = _events(res)
    assert [e["type"] for e in events] == ["agent_start", "agent_end", "done"]
    assert events[0]["origin"] == "direct" and "caller" not in events[0]
