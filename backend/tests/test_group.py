"""Group agent system, offline. The K Electronics app is reached in-process
(TestClient) through the same A2A surface the Group tool uses over HTTP."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.runtime import agent_core
from app.runtime.events import AgentError, collect_events
from app.systems.group import registry as group_registry
from app.systems.group.app import app as group_app
from app.systems.group.tools import request_k_electronics_review as a2a_tool
from app.systems.k_electronics.app import app as k_app


class _ScriptedModel:
    def __init__(self, turns):
        self.turns = list(turns)

    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        return self.turns.pop(0)


def _call(name, idx, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{idx}"}])


K_CHAIN = [  # K Super → Meeting Agent → run_meeting_context_retrieval → answers
    _call("delegate_to_meeting_agent", 11, task="뉴로엣지 회의 결정사항"),
    _call("run_meeting_context_retrieval", 12, query="뉴로엣지 인수", topics=["NeuroEdge"]),
    AIMessage("2026-09-15 전략회의: CFO 2.5조 원 기준"),
    AIMessage("K전자 검토 결과: 조건부 진행"),
]


@pytest.fixture
def scripted(monkeypatch):
    def install(turns):
        model = _ScriptedModel(turns)  # shared by every agent in both systems (calls are sequential)
        monkeypatch.setattr(agent_core, "get_chat_model", lambda: model)

    return install


@pytest.fixture
def k_in_process(monkeypatch):
    """The Group tool's HTTP client talks to the K app in-process."""
    monkeypatch.setattr(a2a_tool, "make_client", lambda: TestClient(k_app))


def test_group_registry_content():
    table = {c.name: (c.kind, set(c.allowed_agents)) for c in group_registry.CAPABILITIES}
    assert table == {
        "subsidiary_review": ("skill", {"group_super"}),
        "request_k_electronics_review": ("tool", {"group_super"}),
    }
    assert set(a2a_tool.request_k_electronics_review.args) == {"request"}


def test_a2a_tool_relays_peer_lifecycle_and_returns_only_the_final_answer(scripted, k_in_process):
    scripted(K_CHAIN)
    response, events = collect_events(lambda: a2a_tool.request_k_electronics_review.invoke({"request": "뉴로엣지 검토"}))
    assert response == "K전자 검토 결과: 조건부 진행"
    assert [e["type"] for e in events] == [
        "a2a_start",
        "agent_start", "delegation_start", "agent_start", "tool_start", "tool_end", "agent_end", "delegation_end", "agent_end",
        "a2a_end",
    ]
    assert events[0] == {"type": "a2a_start", "from": "group_super", "to": "k_super", "request": "뉴로엣지 검토"}
    assert events[-1] == {"type": "a2a_end", "from": "group_super", "to": "k_super", "response": response}
    relayed = events[1:-1]
    assert all(e["relayed"] is True and e["system"] == "k_electronics" and e["caller"] == "group" for e in relayed)


def test_a2a_tool_reports_the_peers_failure_as_the_peer_agent(scripted, k_in_process):
    scripted([AIMessage("제가 보기엔 진행해도 됩니다.")])  # K Super answers without delegating → K error
    with pytest.raises(AgentError) as info:
        collect_events(lambda: a2a_tool.request_k_electronics_review.invoke({"request": "뉴로엣지 검토"}))
    assert info.value.agent == "k_super" and "맡기지 않고" in str(info.value)


def test_a2a_tool_reports_an_unreachable_peer(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(a2a_tool, "make_client", lambda: httpx.Client(transport=httpx.MockTransport(refuse)))
    with pytest.raises(AgentError) as info:
        collect_events(lambda: a2a_tool.request_k_electronics_review.invoke({"request": "뉴로엣지 검토"}))
    assert info.value.agent == "k_super" and "연결할 수 없습니다" in str(info.value)


def _stream(client, path, request):
    with client.stream("POST", path, json={"request": request}) as res:
        assert res.status_code == 200, res.text
        return [json.loads(line) for line in res.iter_lines() if line.strip()]


def test_group_super_end_to_end_through_both_apps(scripted, k_in_process):
    scripted([
        _call("request_k_electronics_review", 1, request="뉴로엣지 인수안을 검토해주세요: 회의 결정사항, 리스크, 타당성, 본실사 여부"),
        *K_CHAIN,
        AIMessage("그룹 투자심의 의견: 조건부 승인"),
    ])
    events = _stream(TestClient(group_app), "/api/agents/group-super/run", "뉴로엣지 인수안을 그룹 투자심의에서 승인할지 판단해.")
    assert [e["type"] for e in events] == [
        "agent_start", "a2a_start",
        "agent_start", "delegation_start", "agent_start", "tool_start", "tool_end", "agent_end", "delegation_end", "agent_end",
        "a2a_end", "agent_end", "done",
    ]
    own = [e for e in events if not e.get("relayed")]
    relayed = [e for e in events if e.get("relayed")]
    group_run = own[0]["run_id"]
    assert all(e["system"] == "group" and e["origin"] == "direct" and e["run_id"] == group_run for e in own)
    # K's events keep K's own stamp, carry the Group run as trace, and are marked relayed.
    assert all(e["system"] == "k_electronics" and e["origin"] == "a2a" and e["trace_id"] == group_run and e["run_id"] != group_run for e in relayed)
    assert events[-3]["response"] == "K전자 검토 결과: 조건부 진행"  # a2a_end: the A2A answer
    assert events[-2] == {**events[-2], "agent": "group_super", "response": "그룹 투자심의 의견: 조건부 승인"}


def test_group_super_direct_small_talk_needs_no_tool(scripted):
    scripted([AIMessage("안녕하세요, 그룹 슈퍼에이전트입니다.")])
    events = _stream(TestClient(group_app), "/api/agents/group-super/run", "안녕?")
    assert [e["type"] for e in events] == ["agent_start", "agent_end", "done"]
