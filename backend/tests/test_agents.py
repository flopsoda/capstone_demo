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
ADVISORY_WORKFLOW = "run_mna_review"


def test_meeting_agent_emits_tool_lifecycle():
    from app.systems.k_electronics.agents.meeting import run_meeting_agent
    from app.runtime.events import collect_events

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
    # The answer was streamed: the tokens after the Tool result add up to the final response.
    tokens = [e["text"] for e in events if e["type"] == "agent_token"]
    assert len(tokens) > 1, tokens
    assert "".join(tokens).strip().endswith(response[-20:]), ("".join(tokens)[-80:], response[-80:])


def test_news_agent_emits_tool_lifecycle():
    from app.systems.k_electronics.agents.news import run_news_agent
    from app.runtime.events import collect_events

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


def test_advisory_agent_emits_tool_lifecycle():
    from app.systems.k_electronics.agents.advisory import run_advisory_agent
    from app.runtime.events import collect_events

    response, events = collect_events(
        lambda: run_advisory_agent(
            "뉴로엣지 인수안의 본실사 진입 여부를 CFO 관점에서 검토해줘. "
            "지난 전략회의에서 CFO는 2.5조 원을 초과하면 가격 타당성을 재검토하기로 했고, "
            "외부 뉴스에서 최대 고객 알파모바일의 공급업체 다변화 검토와 코어NPU 특허소송이 확인됐다."
        )
    )
    types = [e["type"] for e in events]
    assert types[0] == "agent_start" and types[-1] == "agent_end"
    tool_end = next(e for e in events if e["type"] == "tool_end")
    assert tool_end["tool"] == ADVISORY_WORKFLOW
    assert tool_end["result"]["persona"] == "CFO"
    assert tool_end["result"]["recommendation"] == "CONDITIONAL_PROCEED"
    assert tool_end["result"]["context_signals"]["price_ceiling_trillion_krw"] == 2.5
    assert "조건부" in response and "2.3조" in response


def _delegation_blocks(events: list[dict]) -> list[tuple[str, list[str]]]:
    """(target agent, event types inside) for each delegation_start … delegation_end span."""
    blocks = []
    i = 0
    while i < len(events):
        if events[i]["type"] == "delegation_start":
            j = next(k for k in range(i + 1, len(events)) if events[k]["type"] == "delegation_end")
            blocks.append((events[i]["to"], [e["type"] for e in events[i + 1 : j] if e["type"] != "agent_token"]))
            i = j
        i += 1
    return blocks


def test_k_super_delegates_to_each_specialist_in_sequence_then_answers():
    from app.systems.k_electronics.agents.k_super import run_k_super
    from app.runtime.events import collect_events

    response, events = collect_events(lambda: run_k_super(A2A_REQUEST))
    types = [e["type"] for e in events]
    assert types[0] == "agent_start" and events[0]["agent"] == "k_super"
    assert types[-1] == "agent_end" and events[-1]["agent"] == "k_super"
    # Step 6: K Super's own final review, written from its message history — no
    # synthesis tool, no template. It answers the request's last item (본실사 진입 여부).
    assert events[-1]["response"] == response and response
    assert "본실사" in response, response

    blocks = _delegation_blocks(events)
    # Real nesting, one specialist at a time: K Super delegates → specialist runs its tool → returns.
    assert [to for to, _ in blocks] == ["meeting", "news", "advisory"], blocks
    for to, inner in blocks:
        assert inner == ["agent_start", "tool_start", "tool_end", "agent_end"], (to, inner)
    tool_names = [e["tool"] for e in events if e["type"] == "tool_start"]
    assert tool_names == [MEETING_WORKFLOW, NEWS_WORKFLOW, ADVISORY_WORKFLOW]
    tasks = {e["to"]: e["task"] for e in events if e["type"] == "delegation_start"}
    assert all(tasks.values())
    # K Super decided on its own to carry earlier results into the Advisory task
    # (no code builds this text): the meeting's price criterion and the news risks.
    review = next(e for e in events if e["type"] == "tool_end" and e["tool"] == ADVISORY_WORKFLOW)["result"]
    assert review["context_signals"]["price_ceiling_trillion_krw"] == 2.5, tasks["advisory"]
    assert set(review["context_signals"]["confirmed_risks"]) >= {"customer_concentration", "key_personnel"}, tasks["advisory"]


def test_k_super_decides_each_delegation_in_its_own_model_turn():
    from langchain_core.messages import AIMessage, HumanMessage

    from app.runtime.agent_core import RECURSION_LIMIT
    from app.systems.k_electronics.agents.k_super import build_k_super
    from app.runtime.events import collect_events

    state, _ = collect_events(
        lambda: build_k_super().invoke({"messages": [HumanMessage(A2A_REQUEST)]}, config={"recursion_limit": RECURSION_LIMIT})
    )
    turns = [[c["name"] for c in m.tool_calls] for m in state["messages"] if isinstance(m, AIMessage) and m.tool_calls]
    # Meeting result arrives, the model decides again, then News — not both in one turn.
    assert turns == [["delegate_to_meeting_agent"], ["delegate_to_news_agent"], ["delegate_to_advisory_agent"]], turns
    # The run ends because the model answered without a tool call, not because
    # of a termination rule: the last message is a plain AI message.
    last = state["messages"][-1]
    assert isinstance(last, AIMessage) and not last.tool_calls and last.content


def test_k_super_consults_only_the_specialist_the_request_needs():
    """No 'call every registered tool' rule: a request that only asks for past
    meeting decisions is answered after the Meeting Agent alone."""
    from app.systems.k_electronics.agents.k_super import run_k_super
    from app.runtime.events import collect_events

    response, events = collect_events(
        lambda: run_k_super("뉴로엣지 인수와 관련해 과거 경영회의에서 정해진 결정사항과 기준만 정리해서 회신해주세요.")
    )
    assert [e["to"] for e in events if e["type"] == "delegation_start"] == ["meeting"], events
    assert events[-1]["type"] == "agent_end" and events[-1]["agent"] == "k_super"
    assert "2.5조" in response, response


def test_api_streams_ndjson_events():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    with client.stream("POST", "/api/a2a/run", json={"request": A2A_REQUEST, "caller": "test"}) as res:
        assert res.status_code == 200, res.text
        assert res.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in res.iter_lines() if line.strip()]
    types = [e["type"] for e in events]
    assert types[-1] == "done"
    assert [e["to"] for e in events if e["type"] == "delegation_start"] == ["meeting", "news", "advisory"]
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


def test_advisory_direct_chat_answers_concept_question_without_tool():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    with client.stream(
        "POST", "/api/agents/advisory/run", json={"request": "M&A에서 earn-out 조건이 뭔지 개념만 간단히 설명해줘."}
    ) as res:
        assert res.status_code == 200, res.text
        events = [json.loads(line) for line in res.iter_lines() if line.strip()]
    types = [e["type"] for e in events]
    assert "error" not in types, events
    # A general question needs no company data: the agent answers conversationally.
    assert "tool_start" not in types, events
    assert any(e["type"] == "agent_end" and "earn" in e["response"].lower() for e in events)


def test_advisory_direct_chat_uses_tool_for_real_review():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    with client.stream("POST", "/api/agents/advisory/run", json={"request": "뉴로엣지 인수를 CTO 관점에서 검토해줘."}) as res:
        assert res.status_code == 200, res.text
        events = [json.loads(line) for line in res.iter_lines() if line.strip()]
    tool_end = next(e for e in events if e["type"] == "tool_end")
    assert tool_end["tool"] == ADVISORY_WORKFLOW and tool_end["result"]["persona"] == "CTO"


def test_delegation_path_still_requires_tool(monkeypatch):
    from langchain_core.messages import AIMessage

    from app.systems.k_electronics.agents import news
    from app.runtime.events import AgentError, collect_events

    class _NoToolGraph:
        def invoke(self, state, config=None):
            return {"messages": [*state["messages"], AIMessage("안녕하세요, 뉴스 에이전트입니다.")]}

    monkeypatch.setattr(news, "build_news_agent", lambda: _NoToolGraph())
    with pytest.raises(AgentError, match="호출하지 않고"):
        collect_events(lambda: news.run_news_agent("안녕"))
    response, _ = collect_events(lambda: news.run_news_agent("안녕", invocation="direct"))
    assert response == "안녕하세요, 뉴스 에이전트입니다."


def test_k_super_direct_chat_small_talk_does_not_delegate():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    with client.stream("POST", "/api/agents/k-super/run", json={"request": "안녕? 너는 무슨 일을 해?"}) as res:
        assert res.status_code == 200, res.text
        events = [json.loads(line) for line in res.iter_lines() if line.strip()]
    types = [e["type"] for e in events]
    assert "error" not in types, events
    assert "delegation_start" not in types, events
    assert events[-1]["type"] == "done" and any(e["type"] == "agent_end" and e["response"] for e in events)


def test_k_super_direct_chat_delegates_for_a_real_review():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    with client.stream(
        "POST", "/api/agents/k-super/run", json={"request": "뉴로엣지 인수와 관련해 지난 회의에서 정한 기준만 정리해줘."}
    ) as res:
        assert res.status_code == 200, res.text
        events = [json.loads(line) for line in res.iter_lines() if line.strip()]
    assert [e["to"] for e in events if e["type"] == "delegation_start"] == ["meeting"], events
    assert events[-1]["type"] == "done"


def test_group_super_asks_k_electronics_over_a2a_and_writes_the_group_judgment(monkeypatch):
    """Both systems with the real LLM; the Group tool reaches the K app in-process through its A2A surface."""
    from fastapi.testclient import TestClient

    from app.systems.group.app import app as group_app
    from app.systems.group.tools import request_k_electronics_review as a2a_tool
    from app.systems.k_electronics.app import app as k_app

    monkeypatch.setattr(a2a_tool, "make_client", lambda: TestClient(k_app))
    request = (
        "K전자가 검토 중인 뉴로엣지 인수안을 확인해줘. 최근 리스크와 지난 내부 검토 내용을 반영해서 "
        "그룹 투자심의에서 본실사 진입을 승인할지 판단해."
    )
    with TestClient(group_app).stream("POST", "/api/agents/group-super/run", json={"request": request}) as res:
        assert res.status_code == 200, res.text
        events = [json.loads(line) for line in res.iter_lines() if line.strip()]
    types = [e["type"] for e in events]
    assert "error" not in types, events
    assert types[-1] == "done"
    a2a = [e for e in events if e["type"] == "a2a_start"]
    assert len(a2a) == 1 and "뉴로엣지" in a2a[0]["request"], a2a
    # K's own delegations are visible as relayed observation events, in K's order. Group Super may also
    # have delegated to its own specialists first (its call); those are not relayed.
    k_delegations = [e["to"] for e in events if e["type"] == "delegation_start" and e.get("relayed")]
    assert k_delegations == ["meeting", "news", "advisory"], events
    assert all(e.get("relayed") for e in events if e["system"] == "k_electronics")
    assert all(not e.get("relayed") for e in events if e["system"] == "group")
    a2a_end = next(e for e in events if e["type"] == "a2a_end")
    final = next(e for e in events if e["type"] == "agent_end" and e["agent"] == "group_super")
    assert a2a_end["response"] and final["response"]
    assert "본실사" in final["response"] or "승인" in final["response"], final["response"]


def test_group_super_direct_small_talk_does_not_call_a2a():
    from fastapi.testclient import TestClient

    from app.systems.group.app import app as group_app

    with TestClient(group_app).stream("POST", "/api/agents/group-super/run", json={"request": "안녕? 너는 무슨 일을 해?"}) as res:
        assert res.status_code == 200, res.text
        events = [json.loads(line) for line in res.iter_lines() if line.strip()]
    types = [e["type"] for e in events]
    assert "error" not in types and "a2a_start" not in types, events
    assert types[-1] == "done"


def test_group_meeting_agent_direct_chat_uses_group_records():
    from fastapi.testclient import TestClient

    from app.systems.group.app import app as group_app

    with TestClient(group_app).stream(
        "POST", "/api/agents/meeting/run", json={"request": "그룹 투자심의 사전회의에서 정한 기준과 미완료 Action Item을 알려줘."}
    ) as res:
        assert res.status_code == 200, res.text
        events = [json.loads(line) for line in res.iter_lines() if line.strip()]
    tool_end = next(e for e in events if e["type"] == "tool_end")
    assert tool_end["tool"] == MEETING_WORKFLOW and tool_end["result"]["meetings"][0]["title"] == "그룹 투자심의위원회 사전회의"
    final = next(e for e in events if e["type"] == "agent_end")
    assert "2026-09-30" in final["response"] or "투자심의" in final["response"], final["response"]
