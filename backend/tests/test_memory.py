"""Conversation memory of the Super Agents (library/workflows/memory + runtime hook), test database, offline.

The embedding model is a stand-in (character-bigram vectors: texts that share wording are close) so the pgvector
search runs for real without the network; tests/test_live_kg_memory.py does the same with OpenAI.
"""

from __future__ import annotations

import hashlib
import math

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, SystemMessage

from app.library.workflows.memory import reembed
from app.library.workflows.memory.memory import SuperAgentMemory, split_request
from app.library.workflows.memory.store import MemoryStore
from app.library.workflows.news.embed import EmbedUnavailable
from app.runtime import agent_core
from app.runtime.events import new_run, stream_run
from app.runtime.memory import RECALL_TOOL
from app.runtime.schemas import Principal
from app.systems.group import MEETING_STORE as GROUP_STORE, MEMORY as GROUP_MEMORY
from app.systems.group.agents import group_super
from app.systems.k_electronics import MEMORY as K_MEMORY
from app.systems.k_electronics.agents import k_super

DIM = 512


class Bigrams:
    def __init__(self, name="test:bigrams"):
        self.name, self.documents, self.queries = name, 0, 0

    def _vector(self, text: str) -> list[float]:
        v = [0.0] * DIM
        t = "".join(text.lower().split())
        for i in range(len(t) - 1):
            v[int(hashlib.md5(t[i:i + 2].encode()).hexdigest(), 16) % DIM] += 1
        norm = math.sqrt(sum(x * x for x in v)) or 1
        return [x / norm for x in v]

    def embed_query(self, text):
        self.queries += 1
        return self._vector(text)

    def embed_documents(self, texts):
        self.documents += len(texts)
        return [self._vector(t) for t in texts]


class Down(Bigrams):
    def embed_query(self, text):
        raise EmbedUnavailable("test: down")

    def embed_documents(self, texts):
        raise EmbedUnavailable("test: down")


def principal(email: str) -> Principal:
    return Principal(email=email, name=email.split("@")[0], groups=("executive",), clearance=4)


def run(email: str | None = "ceo@example.com", origin: str = "direct"):
    return new_run(system="group", root_agent="group_super", origin=origin, principal=principal(email) if email else None)


@pytest.fixture
def memory(meeting_db, monkeypatch):
    monkeypatch.setenv("MEMORY_ENABLED", "1")
    monkeypatch.setenv("MEMORY_MIN_SIMILARITY", "0.2")
    return SuperAgentMemory(MemoryStore(GROUP_STORE), Bigrams())


def _turns() -> int:
    with GROUP_STORE.connect() as conn:
        return conn.execute("SELECT count(*) AS n FROM mem_turns").fetchone()["n"]


def test_an_exchange_is_kept_once_per_run_with_its_vectors(memory):
    r = run()
    memory.remember(r, "[이전 대화]\n사용자: 안녕\nAI: 안녕하세요\n\n[현재 요청]\n뉴로엣지 인수 검토는 CFO 관점으로 보고해 줘",
                    "알겠습니다. 앞으로 인수 검토는 CFO 관점으로 정리하겠습니다.",
                    [{"tool": "delegate_to_advisory_agent", "task": "뉴로엣지 인수 CFO 관점 검토"}])
    memory.remember(r, "뉴로엣지 인수 검토는 CFO 관점으로 보고해 줘", "다시 온 같은 run", [])  # the same run again: nothing new
    with GROUP_STORE.connect() as conn:
        turn = conn.execute("SELECT * FROM mem_turns").fetchone()
        chunks = conn.execute("SELECT c.text, e.model, vector_dims(e.embedding) AS dims FROM mem_chunks c JOIN mem_embeddings e ON e.chunk_id = c.id").fetchall()
    assert _turns() == 1
    assert turn["user_key"] == "ceo@example.com" and turn["user_text"] == "뉴로엣지 인수 검토는 CFO 관점으로 보고해 줘"  # not the attached history
    assert turn["handoffs"] == [{"tool": "delegate_to_advisory_agent", "task": "뉴로엣지 인수 CFO 관점 검토"}]
    assert len(chunks) == 1 and chunks[0]["model"] == "test:bigrams" and chunks[0]["dims"] == DIM
    assert "맡긴 일: delegate_to_advisory_agent" in chunks[0]["text"]


def test_a_new_session_recalls_the_related_past_oldest_first(memory):
    memory.remember(run(), "뉴로엣지 인수 검토는 앞으로 CFO 관점으로 보고해 줘", "알겠습니다. CFO 관점으로 정리하겠습니다.", [])
    memory.remember(run(), "다음 주 유럽 출장 일정 정리해 줘", "출장 일정은 회의지원 에이전트로 확인하겠습니다.", [])
    memory.remember(run(), "뉴로엣지 인수 검토는 이제 CEO 관점으로 바꿔서 보고해 줘", "알겠습니다. CEO 관점으로 바꾸겠습니다.", [])
    recall = memory.recall(run(), "뉴로엣지 인수 검토 결과 알려줘")  # a new session: no [이전 대화]
    requests = [m["request"] for m in recall.result["memories"]]
    assert requests[:2] == ["뉴로엣지 인수 검토는 앞으로 CFO 관점으로 보고해 줘", "뉴로엣지 인수 검토는 이제 CEO 관점으로 바꿔서 보고해 줘"]
    assert "다음 주 유럽 출장 일정 정리해 줘" not in requests
    assert recall.result["search"]["mode"] == "meaning+keyword" and all("meaning" in m["matched_by"] for m in recall.result["memories"][:2])
    # oldest first, with the time, under the rule that the later instruction wins
    assert recall.context.index("CFO 관점") < recall.context.index("CEO 관점")
    assert "과거 지시끼리 다르면 더 최근 것을 따른다" in recall.context and recall.result["memories"][0]["at"][:2] == "20"


def test_one_user_never_recalls_another_users_conversation(memory):
    memory.remember(run("ceo@example.com"), "뉴로엣지 인수 가격 상한은 2.3조로 기억해 둬", "알겠습니다.", [])
    other = memory.recall(run("staff@example.com"), "뉴로엣지 인수 가격 상한")
    assert other.result["memories"] == [] and other.context == ""
    mine = memory.recall(run("CEO@Example.com"), "뉴로엣지 인수 가격 상한")
    assert [m["request"] for m in mine.result["memories"]] == ["뉴로엣지 인수 가격 상한은 2.3조로 기억해 둬"]


def test_what_the_request_already_carries_is_not_given_twice(memory):
    memory.remember(run(), "뉴로엣지 인수 검토는 CFO 관점으로 보고해 줘", "알겠습니다.", [])
    memory.remember(run(), "뉴로엣지 인수 가격 상한은 2.3조 원이야", "기억하겠습니다.", [])
    request = "[이전 대화]\n사용자: 뉴로엣지 인수 가격 상한은 2.3조 원이야\nAI: 기억하겠습니다.\n\n[현재 요청]\n뉴로엣지 인수 검토 다시 해줘"
    recall = memory.recall(run(), request)
    assert [m["request"] for m in recall.result["memories"]] == ["뉴로엣지 인수 검토는 CFO 관점으로 보고해 줘"]
    assert recall.result["skipped_recent"] == 1


def test_a_short_request_is_searched_with_the_previous_line(memory):
    memory.remember(run(), "브라이트셀 지분 투자 한도는 3천억 원으로 하자", "알겠습니다.", [])
    recall = memory.recall(run(), "[이전 대화]\n사용자: 브라이트셀 지분 투자 건 말인데\nAI: 네.\n\n[현재 요청]\n그거 한도 얼마였지?")
    assert recall.result["query"].startswith("브라이트셀 지분 투자 건 말인데")
    assert [m["request"] for m in recall.result["memories"]] == ["브라이트셀 지분 투자 한도는 3천억 원으로 하자"]


def test_a_model_change_needs_the_new_models_vectors(memory, monkeypatch):
    memory.remember(run(), "뉴로엣지 인수 검토는 CFO 관점으로 보고해 줘", "알겠습니다.", [])
    newer = SuperAgentMemory(memory.store, Bigrams("test:newer"))
    before = newer.recall(run(), "뉴로엣지 인수 검토는 CFO 관점")
    assert all(m["matched_by"] == ["keyword"] for m in before.result["memories"])  # old vectors are not compared
    monkeypatch.setattr(reembed, "memory_embedder", lambda: Bigrams("test:newer"))
    monkeypatch.setattr(reembed, "MemoryStore", lambda _store: memory.store)
    reembed.main(["--schema", "grp"])
    after = newer.recall(run(), "뉴로엣지 인수 검토는 CFO 관점")
    assert "meaning" in after.result["memories"][0]["matched_by"]


def test_an_embedding_model_that_is_down_keeps_the_exchange(memory):
    down = SuperAgentMemory(memory.store, Down())
    down.remember(run(), "뉴로엣지 인수 검토는 CFO 관점으로 보고해 줘", "알겠습니다.", [])
    assert _turns() == 1
    recall = down.recall(run(), "뉴로엣지 인수 검토는 CFO 관점")
    assert recall.result["search"] == {"mode": "keyword", "error": "test: down"} and recall.result["memories"]


def test_split_request():
    assert split_request("그냥 요청") == ("", "그냥 요청")
    assert split_request("[이전 대화]\n사용자: a\nAI: b\n\n[현재 요청]\n지금 요청") == ("사용자: a\nAI: b", "지금 요청")


def test_who_is_remembered(memory, monkeypatch):
    assert memory.applies(run())
    assert not memory.applies(run(origin="a2a"))  # another system's request is not a conversation with a user
    assert not memory.applies(run(None))  # no user
    monkeypatch.setenv("MEMORY_ANONYMOUS_USER", "local-demo")
    assert memory.applies(run(None)) and memory.user_key(run(None)) == "local-demo"
    monkeypatch.setenv("MEMORY_ENABLED", "0")
    assert not memory.applies(run())


# ---------------------------------------------------------------------------
# The Super Agent with its memory (real Super core and delegation Tool; scripted model)
# ---------------------------------------------------------------------------


class _Model:
    def __init__(self, turns):
        self.turns, self.seen = list(turns), []

    def bind_tools(self, _tools):
        return self

    def stream(self, messages):
        self.seen.append(list(messages))
        turn = self.turns.pop(0)
        if isinstance(turn, AIMessage):
            yield turn
            return
        yield AIMessageChunk(content=turn)


def _delegate(task: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "delegate_to_advisory_agent", "args": {"task": task}, "id": "d1"}])


@pytest.fixture
def super_memory(memory, monkeypatch):
    """The Group Super's own memory object, on the test database with the stand-in embedder."""
    monkeypatch.setattr(GROUP_MEMORY, "_embedder", Bigrams())
    advisory_tasks = []

    def advisory(task, invocation="delegation"):
        advisory_tasks.append(task)
        return "경영자문 결과: 조건부 진행"

    monkeypatch.setattr("app.systems.group.agents.advisory.run_advisory_agent", advisory)
    return advisory_tasks


def _chat(email: str, request: str, turns, monkeypatch):
    model = _Model(turns)
    monkeypatch.setattr(agent_core, "get_chat_model", lambda: model)
    events = list(stream_run(lambda: group_super.run_group_super(request), run=new_run(
        system="group", root_agent="group_super", principal=principal(email)), hub=None))
    return model, events


def test_the_super_agent_remembers_and_recalls_in_a_new_session(super_memory, monkeypatch):
    _chat("ceo@example.com", "앞으로 뉴로엣지 인수 검토는 CFO 관점으로 보고해 줘", ["알겠습니다. CFO 관점으로 보고하겠습니다."], monkeypatch)
    assert _turns() == 1
    model, events = _chat("ceo@example.com", "뉴로엣지 인수 검토 결과 알려줘",
                          [_delegate("뉴로엣지 인수 타당성 검토 (사용자 요청: CFO 관점)"), "CFO 관점 검토 결과: 조건부 진행"], monkeypatch)
    kinds = [(e["type"], e.get("tool") or e.get("to")) for e in events if e["type"] in ("tool_start", "tool_end", "delegation_start", "delegation_end")]
    assert kinds == [("tool_start", RECALL_TOOL), ("tool_end", RECALL_TOOL), ("delegation_start", "advisory"), ("delegation_end", "advisory")]
    recalled = next(e for e in events if e["type"] == "tool_end")
    assert recalled["agent"] == "group_super" and [m["request"] for m in recalled["result"]["memories"]] == ["앞으로 뉴로엣지 인수 검토는 CFO 관점으로 보고해 줘"]
    # every model turn of the run saw the memory in its system context; the delegation went on as before
    systems = [m[0] for m in model.seen]
    assert all(isinstance(s, SystemMessage) and "CFO 관점으로 보고해 줘" in s.content for s in systems)
    assert super_memory == ["뉴로엣지 인수 타당성 검토 (사용자 요청: CFO 관점)"]
    assert events[-1]["type"] == "done" and _turns() == 2
    with GROUP_STORE.connect() as conn:
        last = conn.execute("SELECT handoffs FROM mem_turns ORDER BY id DESC LIMIT 1").fetchone()
    assert last["handoffs"] == [{"tool": "delegate_to_advisory_agent", "task": "뉴로엣지 인수 타당성 검토 (사용자 요청: CFO 관점)"}]


def test_another_user_starts_without_that_memory(super_memory, monkeypatch):
    _chat("ceo@example.com", "앞으로 뉴로엣지 인수 검토는 CFO 관점으로 보고해 줘", ["알겠습니다."], monkeypatch)
    model, events = _chat("staff@example.com", "뉴로엣지 인수 검토 결과 알려줘", ["확인할 자료가 없습니다."], monkeypatch)
    recalled = next(e for e in events if e["type"] == "tool_end")
    assert recalled["result"]["memories"] == []
    assert "관련 과거 대화" not in model.seen[0][0].content


def test_a2a_runs_neither_recall_nor_remember(memory, monkeypatch):
    monkeypatch.setattr(K_MEMORY, "_embedder", Bigrams())
    monkeypatch.setattr("app.systems.k_electronics.agents.meeting.run_meeting_agent", lambda task, invocation="delegation": "회의 결과")
    model = _Model([AIMessage(content="", tool_calls=[{"name": "delegate_to_meeting_agent", "args": {"task": "뉴로엣지 회의 기준"}, "id": "m1"}]),
                    "K전자 검토 결과"])
    monkeypatch.setattr(agent_core, "get_chat_model", lambda: model)
    events = list(stream_run(lambda: k_super.run_k_super("뉴로엣지 검토 요청", invocation="a2a"), run=new_run(
        system="k_electronics", root_agent="k_super", origin="a2a", caller="group", principal=principal("ceo@example.com")), hub=None))
    assert not any(e.get("tool") == RECALL_TOOL for e in events) and events[-1]["type"] == "done"
    with K_MEMORY.store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM mem_turns").fetchone()["n"] == 0


def test_a_memory_that_fails_does_not_fail_the_run(super_memory, monkeypatch):
    def broken(run, request):
        raise RuntimeError("store down")

    monkeypatch.setattr(GROUP_MEMORY, "recall", broken)
    monkeypatch.setattr(GROUP_MEMORY, "remember", lambda *a: (_ for _ in ()).throw(RuntimeError("store down")))
    _, events = _chat("ceo@example.com", "안녕", ["안녕하세요"], monkeypatch)
    recalled = next(e for e in events if e["type"] == "tool_end")
    assert recalled["result"] == {"error": "기억을 불러오지 못했습니다: RuntimeError"}
    assert [e["type"] for e in events][-2:] == ["agent_end", "done"]
