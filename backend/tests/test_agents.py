"""Live LLM tests (Ollama Cloud). Skipped when OLLAMA_API_KEY is not configured."""

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


def test_meeting_agent_calls_workflow_tool():
    from app.agents.meeting import run_meeting_agent

    run = run_meeting_agent("뉴로엣지 인수와 관련해 지난 전략회의에서 결정한 기준과 미완료 Action Item을 찾아줘.")
    names = [c["name"] for c in run["tool_calls"]]
    assert "run_meeting_context_retrieval" in names
    assert run["tool_calls"][0]["result"]["matched"] == 1
    assert "2026-09-15" in run["response"]
    assert "2.5조" in run["response"]


def test_k_super_delegates_to_meeting_agent():
    from app.agents.k_super import run_k_super

    delegations = run_k_super(A2A_REQUEST)
    assert [d["agent"] for d in delegations] == ["meeting"]
    d = delegations[0]
    assert d["request"]
    assert any(c["name"] == "run_meeting_context_retrieval" for c in d["tool_calls"])
    assert d["response"]


def test_api_k_super_run():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    res = client.post("/api/k-super/run", json={"request": A2A_REQUEST})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["delegations"][0]["agent"] == "meeting"
    assert body["delegations"][0]["tool_calls"][0]["name"] == "run_meeting_context_retrieval"
