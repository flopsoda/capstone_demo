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

WORKFLOW = "run_meeting_context_retrieval"


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
    assert tool_end["tool"] == WORKFLOW
    assert tool_end["result"]["matched"] == 1
    assert "2026-09-15" in response and "2.5조" in response


def test_k_super_delegates_to_meeting_agent_in_order():
    from app.agents.k_super import run_k_super
    from app.events import collect_events

    _, events = collect_events(lambda: run_k_super(A2A_REQUEST))
    types = [e["type"] for e in events]
    # Real nesting: K Super starts → delegates → Meeting Agent runs its tool → returns → K Super ends.
    expected_order = [
        "agent_start",       # k_super
        "delegation_start",  # k_super → meeting
        "agent_start",       # meeting
        "tool_start",
        "tool_end",
        "agent_end",         # meeting
        "delegation_end",
        "agent_end",         # k_super
    ]
    assert types == expected_order, types
    assert events[1]["to"] == "meeting" and events[1]["task"]
    assert events[3]["tool"] == WORKFLOW


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
    assert "delegation_start" in types and "tool_start" in types
