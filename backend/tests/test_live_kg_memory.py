"""Enterprise KG and conversation memory against the real services: OpenAI embeddings into pgvector, the chat model
(Ollama Cloud, or LLM_PROVIDER=openai) for news extraction and for the agents, on the test database.

Marked `network`; skipped without the keys (MEMORY_EMBED_API_KEY, OLLAMA_API_KEY / OPENAI_API_KEY) or the test
database. A model answers differently from run to run, so these check what must hold, not exact words.
"""

from __future__ import annotations

import json

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
    reviews = [e for e in events if e["type"] == "tool_start" and e["tool"] == "run_mna_review"]
    answer = next(e for e in events if e["type"] == "agent_end")["response"]
    # what the graph says about 알파모바일 reached the numbers (a scenario) or the judgement
    assert "알파모바일" in json.dumps([r["args"] for r in reviews], ensure_ascii=False) + answer, ([r["args"] for r in reviews], answer)


def _article(id_, event, date, headline, summary, companies):
    return {"id": id_, "event": event, "date": date, "source": "테크데일리 (가상)", "headline": headline, "summary": summary,
            "companies": companies, "key_facts": []}


# Made up for the test: one article per kind of relation state, the extension in an event of its own.
STATES_NEWS = {"outlets": {"테크데일리 (가상)": "techdaily.example"}, "articles": [
    _article("T-EXPIRY", "expiry", "2026-09-18", "브라이트셀, X모터스 배터리 공급계약 2027년 6월 만료 예정",
             "브라이트셀은 완성차 고객 X모터스와의 배터리 공급계약이 2027년 6월 만료될 예정이라고 밝혔다. 현재 공급은 정상적으로 이뤄지고 있다.",
             ["브라이트셀", "X모터스"]),
    _article("T-ENDED", "ended", "2026-11-18", "델타센서, 오메가전자와 센서 공급계약 종료",
             "델타센서는 오메가전자에 납품하던 센서 공급계약을 2026년 11월 17일부로 종료했다고 18일 공시했다.", ["델타센서", "오메가전자"]),
    _article("T-REVIEW", "review", "2026-11-10", "베타모터스, 감마칩과 반도체 공급계약 해지 검토",
             "감마칩으로부터 차량용 반도체를 공급받는 베타모터스가 감마칩과의 공급계약 해지를 검토 중인 것으로 알려졌다. 아직 최종 결정은 내려지지 않았다.",
             ["베타모터스", "감마칩"]),
    _article("T-SIGNED", "signed", "2026-09-01", "에타소재, 시그마전지에 양극재 공급계약 체결… 2027년 말까지",
             "에타소재가 시그마전지에 양극재를 공급하는 계약을 체결했다. 계약 기간은 2027년 12월까지다.", ["에타소재", "시그마전지"]),
    _article("T-EXTENDED", "extended", "2026-11-20", "에타소재-시그마전지 양극재 공급계약 2028년까지 연장",
             "에타소재는 시그마전지와의 양극재 공급계약을 2028년 12월까지 연장했다고 20일 밝혔다.", ["에타소재", "시그마전지"]),
]}


@needs_model
def test_the_real_model_and_the_rules_tell_expiry_end_review_and_extension_apart(meeting_db, news_db, tmp_path):
    from app.library.workflows.kg.store import KGStore
    from app.library.workflows.news.store import NewsStore
    from app.systems.k_electronics import DATA_DIR, MEETING_STORE

    (tmp_path / "news.json").write_text(__import__("json").dumps(STATES_NEWS, ensure_ascii=False), encoding="utf-8")
    kg = KGStore(MEETING_STORE, NewsStore("test_kg_states", seed=tmp_path / "news.json"), aliases=DATA_DIR / "companies.json")
    llm.get_chat_model.cache_clear()
    stats = kg.sync(ask_model)
    assert stats["llm_calls"] == 5 and stats["pending_extraction"] == 0, stats
    with kg.connect() as conn:
        facts = [dict(r) for r in conn.execute(
            """SELECT s.label AS s, o.label AS o, f.status, f.valid_to, f.attrs FROM kg_facts f
               JOIN kg_nodes s ON s.id = f.subject JOIN kg_nodes o ON o.id = f.object
               WHERE f.fact_class = 'reported' AND f.predicate = 'SUPPLIES_TO'""")]

    def between(a, b):
        found = [f for f in facts if {a, b} <= {f["s"], f["o"]}]
        assert len(found) == 1, (a, b, facts)
        return found[0]

    expiry = between("브라이트셀", "X모터스")
    assert expiry["status"] == "active" and (expiry["attrs"].get("ends_on") or "").startswith("2027-06"), expiry
    ended = between("델타센서", "오메가전자")
    assert ended["status"] == "ended" and str(ended["valid_to"]) == "2026-11-17", ended
    review = between("베타모터스", "감마칩")
    assert review["status"] == "active" and review["attrs"].get("end_review_since") == "2026-11-10", review
    extended = between("에타소재", "시그마전지")  # two events, one contract
    assert extended["status"] == "active" and (extended["attrs"].get("ends_on") or "").startswith("2028"), extended
