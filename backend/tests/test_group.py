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

    def stream(self, messages):
        yield self.invoke(messages)  # a whole message, not chunks: used as is


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
        "specialist_delegation": ("skill", {"group_super"}),
        "request_k_electronics_review": ("tool", {"group_super"}),
        "delegate_to_meeting_agent": ("tool", {"group_super"}),
        "delegate_to_news_agent": ("tool", {"group_super"}),
        "delegate_to_advisory_agent": ("tool", {"group_super"}),
        "meeting_context_analysis": ("skill", {"meeting"}),
        "meeting_minutes_writing": ("skill", {"meeting"}),
        "meeting_follow_up": ("skill", {"meeting"}),
        "run_meeting_context_retrieval": ("tool", {"meeting"}),
        "save_meeting_minutes": ("tool", {"meeting"}),
        "run_action_item_tracking": ("tool", {"meeting"}),
        "update_action_item": ("tool", {"meeting"}),
        "run_meeting_schedule": ("tool", {"meeting"}),
        "external_risk_analysis": ("skill", {"news"}),
        "run_company_risk_news_analysis": ("tool", {"news"}),
        "mna_feasibility_review": ("skill", {"advisory"}),
        "run_mna_review": ("tool", {"advisory"}),
    }
    assert set(a2a_tool.request_k_electronics_review.args) == {"request"}
    for name in ("delegate_to_meeting_agent", "delegate_to_news_agent", "delegate_to_advisory_agent"):
        assert set(group_registry.get_capability(name).impl.args) == {"task"}


def test_group_agents_bind_their_registry_subset():
    from app.systems.group.agents import advisory, group_super, meeting, news

    for agent in (group_super, meeting, news, advisory):
        assert [t.name for t in agent.TOOLS] == [t.name for t in group_registry.tools_for(agent.AGENT_ID)]
        assert agent.IDENTITY.strip() in agent.SYSTEM_PROMPT
        for skill in group_registry.skills_for(agent.AGENT_ID):
            assert skill.instructions.strip() in agent.SYSTEM_PROMPT
    assert [t.name for t in group_super.TOOLS] == [
        "request_k_electronics_review", "delegate_to_meeting_agent", "delegate_to_news_agent", "delegate_to_advisory_agent",
    ]
    known = {group_super.AGENT_ID, meeting.AGENT_ID, news.AGENT_ID, advisory.AGENT_ID}
    assert all(c.allowed_agents <= known for c in group_registry.CAPABILITIES)


def test_group_delegation_tools_point_at_group_agents():
    from app.systems.group.tools import delegate_to_advisory_agent as da, delegate_to_meeting_agent as dm, delegate_to_news_agent as dn

    assert (dm.FROM_AGENT, dm.TO_AGENT) == ("group_super", "meeting")
    assert (dn.FROM_AGENT, dn.TO_AGENT) == ("group_super", "news")
    assert (da.FROM_AGENT, da.TO_AGENT) == ("group_super", "advisory")
    assert "계열사" in dm.DESCRIPTION and "A2A" in dm.DESCRIPTION  # the Group Super's view of who does what


def test_group_tools_read_group_data_not_k_data(meeting_db):
    from app.systems.k_electronics import registry as k_registry

    g_meet = group_registry.get_capability("run_meeting_context_retrieval").impl
    k_meet = k_registry.get_capability("run_meeting_context_retrieval").impl
    assert g_meet is not k_meet and g_meet.name == k_meet.name
    # Same question, each system's own store.
    g = g_meet.invoke({"query": "그룹 투자심의 사전회의 결정사항", "topics": ["투자심의"]})
    k = k_meet.invoke({"query": "그룹 투자심의 사전회의 결정사항", "topics": ["투자심의"]})
    assert g["meetings"][0]["title"] == "그룹 투자심의위원회 사전회의"
    assert "그룹 투자심의위원회 사전회의" not in [m["title"] for m in k["meetings"]]  # not in K's store
    g_news = group_registry.get_capability("run_company_risk_news_analysis").impl
    assert g_news.invoke({"query": "리스크", "company": "K바이오"})["matched"] == 1
    assert g_news.invoke({"query": "리스크", "company": "뉴로엣지"})["matched"] == 0  # K's domain, not in the Group store
    g_adv = group_registry.get_capability("run_mna_review").impl
    assert g_adv.invoke({"query": "인수 검토", "company": "브라이트셀", "persona": "CFO"})["found"] is True
    assert g_adv.invoke({"query": "인수 검토", "company": "뉴로엣지", "persona": "CFO"})["found"] is False


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


def test_group_super_may_delegate_to_its_own_specialists(scripted, meeting_db):
    """No registry restriction steers Group Super to A2A: delegating to the Group's meeting agent works the same way."""
    scripted([
        _call("delegate_to_meeting_agent", 1, task="그룹 투자심의 사전회의 결정사항"),
        _call("run_meeting_context_retrieval", 2, query="그룹 투자심의 사전회의", topics=["투자심의"]),
        AIMessage("2026-09-30 그룹 투자심의위원회 사전회의: 계열사 검토 결과 전제"),
        AIMessage("그룹 투자심의 기준 정리 완료"),
    ])
    events = _stream(TestClient(group_app), "/api/agents/group-super/run", "그룹 투자심의 사전회의에서 정한 기준만 알려줘.")
    assert [e["type"] for e in events] == [
        "agent_start", "delegation_start", "agent_start", "tool_start", "tool_end", "agent_end", "delegation_end", "agent_end", "done",
    ]
    assert events[1] == {**events[1], "from": "group_super", "to": "meeting"}
    assert events[4]["result"]["meetings"][0]["title"] == "그룹 투자심의위원회 사전회의"
    assert all(e["system"] == "group" and not e.get("relayed") for e in events)


def test_group_specialist_direct_chat_endpoints(scripted):
    scripted([
        _call("run_company_risk_news_analysis", 1, query="K바이오 리스크", company="K바이오"),
        AIMessage("K바이오: 핵심 연구인력 이탈 리스크 HIGH"),
    ])
    events = _stream(TestClient(group_app), "/api/agents/news/run", "K바이오 관련 리스크 알려줘.")
    assert [e["type"] for e in events] == ["agent_start", "tool_start", "tool_end", "agent_end", "done"]
    assert events[2]["result"]["risks"][0]["category"] == "key_personnel"
    assert events[0]["system"] == "group" and events[0]["origin"] == "direct"


def test_a2a_forwards_the_user_and_k_runs_as_that_user_without_write_access(k_in_process, monkeypatch):
    """K applies its own access rules to the Group user it is acting for; the forwarded user never widens anything."""
    import importlib

    from app.library.tools.meeting_access import current_viewer
    from app.runtime.events import current_run, emit, new_run, stream_run
    from app.runtime.schemas import Principal

    k_app_module = importlib.import_module("app.systems.k_electronics.app")
    seen = {}

    def fake_k_super(request, *, invocation):
        seen["run"], seen["viewer"] = current_run(), current_viewer()
        emit({"type": "agent_end", "agent": "k_super", "response": "K전자 검토 결과"})  # the A2A answer
        return "K전자 검토 결과"

    monkeypatch.setattr(k_app_module, "run_k_super", fake_k_super)
    user = Principal(email="ceo@example.com", name="김민수", groups=("executive",), clearance=4)
    run = new_run(system="group", root_agent="group_super", principal=user)
    events = list(stream_run(lambda: a2a_tool.request_k_electronics_review.invoke({"request": "뉴로엣지 검토"}), run=run, hub=None))
    assert events[-1]["type"] == "done"
    assert seen["run"].principal == user and seen["run"].origin == "a2a"
    assert seen["viewer"].clearance == 4 and seen["viewer"].can_write is False
    assert all("principal" not in e for e in events)
