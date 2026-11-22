"""Live LLM tests. Skipped when the configured provider's key (OLLAMA_API_KEY, or OPENAI_API_KEY with LLM_PROVIDER=openai) is not set."""

import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_KEY = "OPENAI_API_KEY" if os.environ.get("LLM_PROVIDER", "").strip().lower() == "openai" else "OLLAMA_API_KEY"
# The meeting and news tools read the test database (tests/conftest.py: meeting and news schemas seeded
# from each system's data/; no publisher feeds); the advisory data is fixture files.
pytestmark = [pytest.mark.skipif(not os.environ.get(_KEY), reason=f"{_KEY} not set"), pytest.mark.usefixtures("meeting_db", "news_db"),
              pytest.mark.network]

A2A_REQUEST = (
    "뉴로엣지 인수안에 대해 계열사 차원의 예비 검토를 수행해주세요.\n"
    "다음을 포함해주세요: 과거 경영회의 결정사항, 최근 외부 리스크, 재무 및 전략적 인수 타당성, 본실사 진입 여부"
)

MEETING_WORKFLOW = "run_meeting_context_retrieval"
NEWS_WORKFLOW = "run_company_risk_news_analysis"
ADVISORY_WORKFLOW = "run_mna_review"
KG_TOOL = "query_enterprise_kg"  # the advisory agent looks the company up in the graph before the review


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
    assert [e["tool"] for e in events if e["type"] == "tool_start"] == [KG_TOOL, ADVISORY_WORKFLOW]
    tool_end = next(e for e in events if e["type"] == "tool_end" and e["tool"] == ADVISORY_WORKFLOW)
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
        tools = ["tool_start", "tool_end"] * (2 if to == "advisory" else 1)  # advisory: the graph, then the review
        assert inner == ["agent_start", *tools, "agent_end"], (to, inner)
    tool_names = [e["tool"] for e in events if e["type"] == "tool_start"]
    assert tool_names == [MEETING_WORKFLOW, NEWS_WORKFLOW, KG_TOOL, ADVISORY_WORKFLOW]
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
    tool_end = next(e for e in events if e["type"] == "tool_end" and e["tool"] == ADVISORY_WORKFLOW)
    assert tool_end["result"]["persona"] == "CTO"


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


def _group_super_first_handoff(request: str) -> str:
    """The Group Super's first handoff for `request` (one model turn; nothing is run)."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.runtime.agent_core import SUPER_INVOCATION_CONTEXT
    from app.runtime.llm import get_chat_model
    from app.systems.group.agents import group_super

    model = get_chat_model().bind_tools(group_super.TOOLS)
    message = model.invoke([SystemMessage(f"{group_super.SYSTEM_PROMPT}\n\n{SUPER_INVOCATION_CONTEXT['direct']}"), HumanMessage(request)])
    return message.tool_calls[0]["name"] if message.tool_calls else ""


@pytest.mark.parametrize("request_text, handoff", [
    # A meeting held at the subsidiary is the subsidiary's record, even when the group also discussed the deal.
    ("거, 저번에 K전자에서 인수한다고 회의했던 거 있잖아. 그거 뭐였지?", "request_k_electronics_review"),
    ("K전자 쪽 회의에서 나온 후속 업무들 지금 어떻게 되고 있어?", "request_k_electronics_review"),
    ("그룹 투자심의 사전회의에서 뉴로엣지 건 뭐라고 정했지?", "delegate_to_meeting_agent"),
])
def test_group_super_routes_by_who_owns_the_record(request_text, handoff):
    assert _group_super_first_handoff(request_text) == handoff


_STATUS_REPORTED = (  # the gateway's history format (navitech agents/remote.py build_request)
    "[이전 대화]\n사용자: K전자 뉴로엣지 인수 건 후속 업무는 지금 어떻게 되고 있어?\n"
    "AI: K전자에 확인한 결과 9/15 AI 반도체 전략회의의 후속 업무 중 적정 기업가치 산정(박지훈 CFO, 기한 10/10)은 39일 지연 · 미착수, "
    "핵심인력 Retention 확인(이서연 CTO)은 협의가 막혀 18일 지연, 고객사 의존도 확인은 진행 중입니다.\n\n[현재 요청]\n"
)


@pytest.mark.parametrize("utterance, mails", [
    ("담당자한테 어떻게 된 건지 물어봐.", True),   # an indirect ask to check with the owners: the reminder goes out
    ("이건 담당자들한테 직접 들어 봐야겠다.", True),
    ("왜 이렇게 늦어지는 거지? 한번 확인해 봐야겠다.", True),  # a question with a wish to check: ask the owners why
    ("왜 아직 안 끝났어?", False),              # a question alone: look it up, no mail
    ("생각보다 많이 늦네.", False),             # a remark: answer only
])
def test_group_super_reads_a_reminder_intent_from_the_conversation(utterance, mails):
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.runtime.agent_core import SUPER_INVOCATION_CONTEXT
    from app.runtime.llm import get_chat_model
    from app.systems.group.agents import group_super

    model = get_chat_model().bind_tools(group_super.TOOLS)
    message = model.invoke([SystemMessage(f"{group_super.SYSTEM_PROMPT}\n\n{SUPER_INVOCATION_CONTEXT['direct']}"),
                            HumanMessage(_STATUS_REPORTED + utterance)])
    asks = [c["args"].get("request", "") for c in message.tool_calls if c["name"] == "request_k_electronics_review"]
    assert any("메일" in a for a in asks) is mails, (utterance, message.tool_calls or message.content)
    if mails:  # a self-contained order: the items and owners, not "그거"
        assert "박지훈" in asks[0] or "기업가치" in asks[0]


def test_no_reminder_goes_out_for_owners_nobody_named(monkeypatch):
    """Group app → K app over A2A with the real LLM: only the deal was discussed, then "the owners need checking".
    Whatever the Group Super writes, K mails only items its request names — here none, so no mail is queued."""
    from fastapi.testclient import TestClient

    from app.systems.group.app import app as group_app
    from app.systems.group.tools import request_k_electronics_review as a2a_tool
    from app.systems.k_electronics import MEETING_STORE
    from app.systems.k_electronics.app import app as k_app

    monkeypatch.setenv("MEETINGS_CONFIRM_GROUPS", "*")
    monkeypatch.setattr(a2a_tool, "make_client", lambda: TestClient(k_app))
    request = ("[이전 대화]\n사용자: K전자가 검토 중인 뉴로엣지 인수안에 대해 알려 줘.\n"
               "AI: 요구가 2.8조 원으로 적정가치(2.0~2.3조 원)를 웃돌아 조건부 진행 의견입니다.\n\n[현재 요청]\n담당자한테 확인이 필요하겠는데.")
    with TestClient(group_app).stream("POST", "/api/agents/group-super/run", json={"request": request}) as res:
        events = [json.loads(line) for line in res.iter_lines() if line.strip()]
    assert events[-1]["type"] == "done", events[-1]
    with MEETING_STORE.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox WHERE dedupe_key LIKE 'nudge:%%'").fetchone()["n"] == 0


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


TRANSCRIPT = """(화자 자동 식별 transcript)
[00:00] 김민수 (CEO): 오늘은 뉴로엣지 협상 전략을 정하겠습니다.
[00:10] 박지훈 (CFO): 실사 결과 고객 집중도가 38%라서, 가격 상한을 2.3조 원으로 낮춰야 합니다.
[00:25] 김민수 (CEO): 동의합니다. 가격 상한은 2.3조 원으로 하겠습니다.
[00:32] 이서연 (CTO): 핵심 설계팀 Retention 패키지는 인사팀이 11월 14일까지 설계해 주세요.
[00:45] 미확인 화자 1: 최대 고객사와의 장기 계약 갱신 여부는 아직 모릅니다."""


def test_meeting_agent_saves_a_transcript_as_a_draft():
    from app.runtime.events import collect_events
    from app.systems.k_electronics.agents.meeting import run_meeting_agent

    response, events = collect_events(lambda: run_meeting_agent(
        f"2026-10-24 뉴로엣지 협상 전략회의 녹음입니다. 회의록 초안을 작성해서 저장해줘. 대외비(3)로.\n\n{TRANSCRIPT}", invocation="direct"))
    saved = [e for e in events if e["type"] == "tool_end" and e["tool"] == "save_meeting_minutes"]
    assert saved, [e.get("tool") for e in events if e["type"] == "tool_end"]
    result = saved[-1]["result"]
    assert result["saved"] and result["status"] == "draft"  # never finalized without being asked
    assert any("2.3조" in d["statement"] for d in result["record"]["decisions"])
    assert result["meeting_id"] in response


def test_meeting_agent_tracks_overdue_items():
    from app.runtime.events import collect_events
    from app.systems.k_electronics.agents.meeting import run_meeting_agent

    response, events = collect_events(lambda: run_meeting_agent("기한이 지난 Action Item 알려줘.", invocation="direct"))
    tracked = [e for e in events if e["type"] == "tool_end" and e["tool"] == "run_action_item_tracking"]
    assert tracked and tracked[-1]["result"]["summary"]["overdue"] >= 1
    assert "적정 기업가치 산정" in response
