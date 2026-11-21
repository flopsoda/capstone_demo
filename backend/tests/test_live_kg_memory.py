"""Enterprise KG and conversation memory against the real services: OpenAI embeddings into pgvector, the chat model
(Ollama Cloud, or LLM_PROVIDER=openai) for news extraction and for the agents, on the test database.

Marked `network`; skipped without the keys (MEMORY_EMBED_API_KEY, OLLAMA_API_KEY / OPENAI_API_KEY) or the test
database. A model answers differently from run to run, so these check what must hold, not exact words.
"""

from __future__ import annotations

import os

import pytest

from app.library.tools.correct_transcript import ask_model
from app.library.workflows import outbox
from app.library.workflows.memory.embedding import memory_embedder
from app.library.workflows.memory.memory import SuperAgentMemory
from app.library.workflows.memory.store import MemoryStore
from app.runtime import llm
from app.runtime.events import new_run, stream_run
from app.runtime.schemas import Principal
from app.systems.group import MEETING_STORE as GROUP_STORE, MEMORY as GROUP_MEMORY
from app.systems.group.agents import group_super
from app.systems.k_electronics import KG_STORE
from app.systems.k_electronics.agents import advisory as k_advisory

pytestmark = pytest.mark.network


def _chat_model_configured() -> bool:
    if os.environ.get("LLM_PROVIDER", "").strip().lower() == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    return bool(os.environ.get("OLLAMA_API_KEY"))


needs_embeddings = pytest.mark.skipif(not os.environ.get("MEMORY_EMBED_API_KEY"), reason="MEMORY_EMBED_API_KEY is not set")
needs_model = pytest.mark.skipif(not _chat_model_configured(), reason="no chat model key")


def principal(email: str) -> Principal:
    return Principal(email=email, name=email.split("@")[0], groups=("executive",), clearance=4)


def run(email: str):
    return new_run(system="group", root_agent="group_super", principal=principal(email))


@pytest.fixture
def live_memory(meeting_db, monkeypatch):
    monkeypatch.setenv("MEMORY_ENABLED", "1")
    monkeypatch.setenv("MEMORY_EMBED_PROVIDER", "openai")
    return SuperAgentMemory(MemoryStore(GROUP_STORE))


@needs_embeddings
def test_openai_vectors_in_pgvector_find_a_past_exchange_in_other_words(live_memory):
    assert live_memory.embedder().name == "openai:text-embedding-3-small"
    live_memory.remember(run("ceo@example.com"), "앞으로 뉴로엣지 인수 검토 보고는 CFO 관점으로 요약해 줘", "알겠습니다. CFO 관점으로 정리하겠습니다.", [])
    live_memory.remember(run("ceo@example.com"), "다음 주 유럽 출장 일정 정리해 줘", "출장 일정은 회의지원 에이전트로 확인하겠습니다.", [])
    live_memory.remember(run("ceo@example.com"), "지난번 K바이오 연구인력 이탈 건 담당자한테 리마인더 보냈어?", "오늘 리마인더 메일을 보냈습니다.", [])
    live_memory.remember(run("staff@example.com"), "NeuroEdge 재무 검토 결과 요약해 줘", "재무 검토 결과입니다.", [])
    with GROUP_STORE.connect() as conn:
        dims = {r["d"] for r in conn.execute("SELECT DISTINCT vector_dims(embedding) AS d FROM mem_embeddings")}
    assert dims == {1536}
    recall = live_memory.recall(run("ceo@example.com"), "NeuroEdge 딜 재무 쪽 의견 정리 부탁해")  # no shared Korean company name
    assert recall.result["search"]["mode"] == "meaning+keyword"
    found = recall.result["memories"]
    assert found and found[0]["request"] == "앞으로 뉴로엣지 인수 검토 보고는 CFO 관점으로 요약해 줘" and "meaning" in found[0]["matched_by"]
    assert all(m["request"] != "NeuroEdge 재무 검토 결과 요약해 줘" for m in found)  # another user's
    assert "CFO 관점" in recall.context


@needs_embeddings
@needs_model
def test_the_group_super_carries_a_remembered_instruction_into_its_delegation(live_memory, monkeypatch):
    """Session 1 leaves an instruction; session 2 (no history attached) asks for a review: the Group Super, with the
    real model, passes the remembered perspective on to the advisory agent (stubbed here to catch the task)."""
    monkeypatch.setattr(GROUP_MEMORY, "_embedder", None)  # the configured provider: OpenAI
    tasks = []
    monkeypatch.setattr("app.systems.group.agents.advisory.run_advisory_agent",
                        lambda task, invocation="delegation": tasks.append(task) or "브라이트셀 인수 검토: 조건부 진행 (가격 조정 필요)")
    llm.get_chat_model.cache_clear()

    def chat(request: str) -> list[dict]:
        return list(stream_run(lambda: group_super.run_group_super(request), run=run("ceo@example.com"), hub=None))

    first = chat("앞으로 경영자문 에이전트에게 인수 검토를 맡길 때는 항상 CFO 관점으로 검토해 달라고 해 줘. 지금은 알겠다고만 답해.")
    assert first[-1]["type"] == "done"
    second = chat("그룹이 검토 중인 브라이트셀 인수안, 경영자문 에이전트에게 검토 맡겨줘.")
    assert second[-1]["type"] == "done", second[-1]
    recalled = next(e for e in second if e["type"] == "tool_end" and e["tool"] == "recall_conversation_memory")
    assert any("CFO" in m["request"] for m in recalled["result"]["memories"])
    assert tasks and "CFO" in tasks[0], tasks
    other = list(stream_run(lambda: group_super.run_group_super("브라이트셀 인수안 검토 맡겨줘"), run=run("staff@example.com"), hub=None))
    assert next(e for e in other if e["type"] == "tool_end")["result"]["memories"] == []


@needs_model
def test_the_advisory_agent_builds_on_the_graph_with_the_real_model(meeting_db, news_db, monkeypatch):
    """Real extraction over the K Electronics news, then the K advisory agent asked about NeuroEdge in a direct chat."""
    monkeypatch.setattr(outbox, "lookup_email", lambda name: None)
    llm.get_chat_model.cache_clear()
    stats = KG_STORE.sync(ask_model)
    assert stats["llm_calls"] >= 1 and stats["extracted"] >= 1, stats
    graph = KG_STORE.query(__import__("app.library.workflows.meeting_store", fromlist=["Viewer"]).Viewer(
        "ceo@example.com", "김민수", frozenset({"executive"}), 4, True), "뉴로엣지", ["뉴로엣지"])
    assert graph["found"] and graph["decisions"] and graph["reported_facts"], graph
    assert any("알파모바일" in (f["subject"], f["object"]) for f in graph["reported_facts"]), graph["reported_facts"]
    events = list(stream_run(lambda: k_advisory.run_advisory_agent(
        "뉴로엣지 인수 타당성을 CFO 관점에서 검토해줘. 최근 뉴스와 지난 회의에서 정한 기준이 어떻게 연결되는지도 반영해줘.", invocation="direct"),
        run=new_run(system="k_electronics", root_agent="advisory", principal=principal("ceo@example.com")), hub=None))
    assert events[-1]["type"] == "done", events[-1]
    tools = [e["tool"] for e in events if e["type"] == "tool_start"]
    assert "query_enterprise_kg" in tools and "run_mna_review" in tools, tools
    review = next(e for e in events if e["type"] == "tool_start" and e["tool"] == "run_mna_review")
    answer = next(e for e in events if e["type"] == "agent_end")["response"]
    assert "알파모바일" in (review["args"].get("context") or "") + answer, (review["args"], answer)
