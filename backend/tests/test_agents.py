"""Live LLM tests (Ollama Cloud). Skipped when OLLAMA_API_KEY is not configured."""

import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

pytestmark = pytest.mark.skipif(not os.environ.get("OLLAMA_API_KEY"), reason="OLLAMA_API_KEY not set")

A2A_REQUEST = (
    "뉴로엣지 인수안에 대해 계열사 차원의 예비 검토를 수행해주세요.\n"
    "다음을 포함해주세요: 과거 경영회의 결정사항, 최근 외부 리스크, 재무 및 전략적 인수 타당성, 본실사 진입 여부"
)

MEETING_WORKFLOW = "run_meeting_context_retrieval"
NEWS_WORKFLOW = "run_company_risk_news_analysis"


def test_meeting_agent_emits_tool_lifecycle():
    from app.agents.meeting import run_meeting_agent
    from app.events import collect_events

    response, events = collect_events(
        lambda: run_meeting_agent("뉴로엣지 인수와 관련해 지난 전략회의에서 결정한 기준과 미완료 Action Item을 찾아줘.")
    )
    types = [e["type"] for e in events]
    assert types[0] == "agent_start" and types[-1] == "agent_end"
    assert "tool_start" in types and "tool_end" in types
    tool_end = next(e for e in events if e["type"] == "tool_end")
    assert tool_end["tool"] == MEETING_WORKFLOW
    assert tool_end["result"]["matched"] == 1
    assert "2026-09-15" in response and "2.5조" in response


def test_news_agent_emits_tool_lifecycle():
    from app.agents.news import run_news_agent
    from app.events import collect_events

    response, events = collect_events(
        lambda: run_news_agent(
            "뉴로엣지 인수 판단에 영향을 줄 수 있는 최근 외부 리스크를 조사해줘. "
            "주요 고객, 법적 분쟁, 핵심인력 관련 이슈를 우선 확인해."
        )
    )
    types = [e["type"] for e in events]
    assert types[0] == "agent_start" and types[-1] == "agent_end"
    assert "tool_start" in types and "tool_end" in types
    tool_end = next(e for e in events if e["type"] == "tool_end")
    assert tool_end["tool"] == NEWS_WORKFLOW
    assert tool_end["result"]["matched"] == 3
    assert "알파모바일" in response and "46%" in response
    assert "코어NPU" in response and "CTO" in response


def _delegation_blocks(events: list[dict]) -> list[tuple[str, list[str]]]:
    """(target agent, event types inside) for each delegation_start … delegation_end span."""
    blocks = []
    i = 0
    while i < len(events):
        if events[i]["type"] == "delegation_start":
            j = next(k for k in range(i + 1, len(events)) if events[k]["type"] == "delegation_end")
            blocks.append((events[i]["to"], [e["type"] for e in events[i + 1 : j]]))
            i = j
        i += 1
    return blocks


def test_k_super_delegates_to_each_specialist_in_sequence():
    from app.agents.k_super import run_k_super
    from app.events import collect_events

    _, events = collect_events(lambda: run_k_super(A2A_REQUEST))
    types = [e["type"] for e in events]
    assert types[0] == "agent_start" and events[0]["agent"] == "k_super"
    assert types[-1] == "agent_end" and events[-1]["agent"] == "k_super"

    blocks = _delegation_blocks(events)
    # Real nesting, one specialist at a time: K Super delegates → specialist runs its tool → returns.
    assert [to for to, _ in blocks] == ["meeting", "news"], blocks
    for to, inner in blocks:
        assert inner == ["agent_start", "tool_start", "tool_end", "agent_end"], (to, inner)
    tool_names = [e["tool"] for e in events if e["type"] == "tool_start"]
    assert tool_names == [MEETING_WORKFLOW, NEWS_WORKFLOW]
    assert all(e["task"] for e in events if e["type"] == "delegation_start")


def test_k_super_decides_each_delegation_in_its_own_model_turn():
    from langchain_core.messages import AIMessage, HumanMessage

    from app.agents.common import RECURSION_LIMIT
    from app.agents.k_super import build_k_super
    from app.events import collect_events

    state, _ = collect_events(
        lambda: build_k_super().invoke({"messages": [HumanMessage(A2A_REQUEST)]}, config={"recursion_limit": RECURSION_LIMIT})
    )
    turns = [[c["name"] for c in m.tool_calls] for m in state["messages"] if isinstance(m, AIMessage) and m.tool_calls]
    # Meeting result arrives, the model decides again, then News — not both in one turn.
    assert turns == [["delegate_to_meeting_agent"], ["delegate_to_news_agent"]], turns
    # The run ends right after the last delegation result (no synthesis yet).
    assert not isinstance(state["messages"][-1], AIMessage)


def test_api_streams_ndjson_events():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    with client.stream("POST", "/api/k-super/run", json={"request": A2A_REQUEST}) as res:
        assert res.status_code == 200, res.text
        assert res.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in res.iter_lines() if line.strip()]
    types = [e["type"] for e in events]
    assert types[-1] == "done"
    assert [e["to"] for e in events if e["type"] == "delegation_start"] == ["meeting", "news"]
    assert "tool_start" in types


def test_news_api_streams_ndjson_events():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    with client.stream("POST", "/api/agents/news/run", json={"request": "뉴로엣지 관련 악재만 다시 보여줘."}) as res:
        assert res.status_code == 200, res.text
        events = [json.loads(line) for line in res.iter_lines() if line.strip()]
    types = [e["type"] for e in events]
    assert types[-1] == "done"
    assert next(e for e in events if e["type"] == "tool_start")["tool"] == NEWS_WORKFLOW


def test_direct_chat_small_talk_does_not_require_tool():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    with client.stream("POST", "/api/agents/news/run", json={"request": "안녕? 너는 누구야?"}) as res:
        assert res.status_code == 200, res.text
        events = [json.loads(line) for line in res.iter_lines() if line.strip()]
    types = [e["type"] for e in events]
    # Direct chat: no Workflow Tool call is not a failure — the run ends normally.
    assert "error" not in types, events
    assert types[-1] == "done"
    assert any(e["type"] == "agent_end" and e["response"] for e in events)


def test_delegation_path_still_requires_tool(monkeypatch):
    from langchain_core.messages import AIMessage

    from app.agents import news
    from app.events import AgentError, collect_events

    class _NoToolGraph:
        def invoke(self, state, config=None):
            return {"messages": [*state["messages"], AIMessage("안녕하세요, 뉴스 에이전트입니다.")]}

    monkeypatch.setattr(news, "build_news_agent", lambda: _NoToolGraph())
    with pytest.raises(AgentError, match="호출하지 않고"):
        collect_events(lambda: news.run_news_agent("안녕"))
    response, _ = collect_events(lambda: news.run_news_agent("안녕", require_tool=False))
    assert response == "안녕하세요, 뉴스 에이전트입니다."
