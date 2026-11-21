"""Enterprise KG (library/workflows/kg) on the K Electronics system's seeded meetings and news, test database.

The news extraction model is a script (no network): it answers per news event, like the real one would. These pin
what the graph promises: meetings and news meet in one entity; the same data twice writes nothing; reports of one
event are one fact while another event between the same parties is another; edits in the sources reach the graph;
what a user may not see in the meeting store is not read through the graph; an association is never an assessed
influence; the systems' graphs are apart; a different resolver re-resolves without asking the model again.
"""

from __future__ import annotations

import json
import threading

import psycopg
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from app.library.workflows import action_items, decision_watch, meeting_minutes, outbox
from app.library.workflows.kg import resolve
from app.library.workflows.meeting_minutes import save_minutes
from app.library.workflows.meeting_store import Viewer
from app.runtime import agent_core
from app.runtime.events import collect_events
from app.systems.group import KG_STORE as GROUP_KG
from app.systems.k_electronics import DATA_DIR, KG_STORE, MEETING_STORE

EXEC = Viewer("ceo@example.com", "김민수", frozenset({"executive"}), 4, True)
STAFF = Viewer("staff@example.com", "박지훈", frozenset({"staff"}), 2, True)
SUPPLY_EVENT = "공급업체 다변화"  # words of the alphamobile event's first headline
PATENT_EVENT = "특허침해 소송"
ENGINEERS_EVENT = "독립적인 연구환경"


def _relation(s, p, o, status, date, article, expression, evidence=""):
    return {"subject": s, "predicate": p, "object": o, "status": status, "date": date, "article_ids": [article],
            "expression": expression, "evidence": evidence or expression}


NEUROEDGE = {"name": "뉴로엣지", "type": "organization", "aliases": ["NeuroEdge"]}
ALPHA = {"name": "알파모바일", "type": "organization", "aliases": ["AlphaMobile"]}
CORE = {"name": "코어NPU", "type": "organization", "aliases": ["CoreNPU"]}

ANSWERS = {
    SUPPLY_EVENT: {"entities": [NEUROEDGE, ALPHA], "relations": [
        _relation("뉴로엣지", "SUPPLIES_TO", "알파모바일", "active", "2026-10-02", "NEWS-2026-10-02-01", "최대 고객사"),
        # the other direction, in the extractor's other words: the resolver turns it around (CUSTOMER_OF → SUPPLIES_TO)
        _relation("알파모바일", "CUSTOMER_OF", "뉴로엣지", "ended", "2026-11-17", "NEWS-2026-11-17-01", "공급계약 중단")]},
    PATENT_EVENT: {"entities": [CORE, NEUROEDGE], "relations": [
        _relation("코어NPU", "SUES", "뉴로엣지", "active", "2026-09-24", "NEWS-2026-09-24-01", "특허침해 소송 제기"),
        _relation("코어NPU", "가처분", "뉴로엣지", "active", "2026-10-08", "NEWS-2026-10-08-01", "판매금지 가처분 추가 신청")]},
    # A different matter between the same companies (made up for the test): a second lawsuit, its own event.
    ENGINEERS_EVENT: {"entities": [CORE, NEUROEDGE], "relations": [
        _relation("코어NPU", "SUES", "뉴로엣지", "active", "2026-09-30", "NEWS-2026-09-30-01", "인력 빼가기 소송")]},
}


class Extractor:
    def __init__(self):
        self.calls = 0

    def __call__(self, system, user):
        self.calls += 1
        first = user.split("\n", 1)[0] + "\n" + user
        for words, answer in ANSWERS.items():
            if words in first:
                return json.dumps(answer, ensure_ascii=False)
        return json.dumps({"entities": [], "relations": []})


@pytest.fixture
def kg(meeting_db, news_db, monkeypatch):
    monkeypatch.setattr(outbox, "lookup_email", lambda name: None)
    return KG_STORE


def _count(table: str) -> int:
    with KG_STORE.connect() as conn:
        return conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]


def _facts(predicate: str) -> list[dict]:
    with KG_STORE.connect() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT f.*, (SELECT count(*) FROM kg_fact_support s WHERE s.fact_id = f.id) AS supports
               FROM kg_facts f WHERE predicate = %s ORDER BY first_seen""", (predicate,))]


def test_meetings_and_news_meet_in_one_entity(kg):
    stats = kg.sync(Extractor())
    assert stats["meetings"] == 3 and stats["news_events"] >= 6 and stats["pending_extraction"] == 0
    with kg.connect() as conn:
        node = conn.execute("SELECT * FROM kg_nodes WHERE id = 'org:뉴로엣지'").fetchone()
        assert node["type"] == "organization" and {"뉴로엣지", "NeuroEdge"} <= set(node["attrs"]["aliases"])
        # the seed (companies.json), the meeting's topics and every news source name the same node
        links = conn.execute("""SELECT DISTINCT m.source_kind FROM kg_mention_links l JOIN kg_mentions m ON m.id = l.mention_id
                                WHERE l.node_id = 'org:뉴로엣지'""").fetchall()
    assert {r["source_kind"] for r in links} == {"meeting", "news_event"}
    out = kg.query(EXEC, "뉴로엣지 인수 검토")
    assert [e["id"] for e in out["entities"]] == ["org:뉴로엣지"]
    assert [m["id"] for m in out["meetings"]] == ["MTG-2026-09-15"]
    assert {d["ref"] for d in out["decisions"]} == {"MTG-2026-09-15-D1", "MTG-2026-09-15-D2", "MTG-2026-09-15-D3"}
    assert any(f["text"] == "핵심인력 Retention 확인" and f["status"] == "blocked" for f in out["follow_ups"])
    assert out["news_events"] and out["associations"]
    assert all(a["basis"].endswith("영향 판정 아님") for a in out["associations"])
    assert out["assessed_impacts"] == []  # nothing judged yet: associations only


def test_same_data_twice_writes_nothing(kg):
    model = Extractor()
    kg.sync(model)
    calls = model.calls
    tables = ("kg_nodes", "kg_mentions", "kg_claims", "kg_facts", "kg_fact_support", "kg_aliases", "kg_extractions")
    before = {t: _count(t) for t in tables}
    with kg.connect() as conn:
        ids = {r["id"] for r in conn.execute("SELECT id FROM kg_facts")}
    again = kg.sync(model)
    assert (again["meetings"], again["news_events"], again["rounds"]) == (0, 0, 1)
    assert model.calls == calls  # nothing sent to the model again
    assert {t: _count(t) for t in tables} == before
    with kg.connect() as conn:
        assert {r["id"] for r in conn.execute("SELECT id FROM kg_facts")} == ids


def test_one_event_is_one_fact_and_another_event_is_another(kg):
    kg.sync(Extractor())
    supply = _facts("SUPPLIES_TO")
    assert len(supply) == 1 and supply[0]["supports"] == 2  # two articles, two wordings, one direction
    assert (supply[0]["subject"], supply[0]["object"]) == ("org:뉴로엣지", "org:알파모바일")
    # the later report ended it
    assert supply[0]["status"] == "ended" and str(supply[0]["valid_to"]) == "2026-11-17"
    assert supply[0]["attrs"]["expressions"] == ["공급계약 중단", "최대 고객사"]
    suits = _facts("SUES")
    assert len(suits) == 2  # same companies, same relation, two matters
    assert len({s["instance_key"] for s in suits}) == 2
    patent = next(s for s in suits if s["supports"] == 2)  # 소송 and 가처분 (normalized to SUES) of one matter
    assert patent["status"] == "active"
    out = kg.query(EXEC, "코어NPU", entities=["CoreNPU"])
    assert {(f["predicate"], f["object"]) for f in out["reported_facts"]} == {("SUES", "뉴로엣지")}
    assert len(out["reported_facts"]) == 2


def test_source_edits_reach_the_graph(kg):
    kg.sync(Extractor())
    # an action item finished, a later meeting replaces a decision (with a premise the decision watch can judge)
    assert action_items.update_action_item(store=MEETING_STORE, viewer=EXEC, item_id="MTG-2026-09-15-A3", status="done")["updated"]
    saved = save_minutes(
        store=MEETING_STORE, viewer=EXEC, title="뉴로엣지 인수 가격 재검토", date="2026-10-20", topics=["뉴로엣지", "인수"],
        summary="가격 상한을 낮췄다.", classification=3, finalize=True,
        attendees=[{"name": "김민수", "title": "CEO", "role": "chair"}, {"name": "박지훈", "title": "CFO"}],
        decisions=[{"kind": "criterion", "statement": "뉴로엣지 인수가격 상한을 2.3조 원으로 낮춤", "speaker": "박지훈 (CFO)",
                    "supersedes": "MTG-2026-09-15-D1",
                    "premises": [{"kind": "premise", "statement": "알파모바일 공급계약이 유지된다", "speaker": "박지훈 (CFO)",
                                  "watch": ["알파모바일", "공급계약"]}]}],
        action_items=[{"text": "알파모바일 공급계약 갱신 여부 확인", "owner": "박지훈", "due": "2026-11-30"}])
    assert saved.get("finalized"), saved.get("warnings")
    stats = kg.sync(Extractor())
    assert stats["meetings"] >= 2  # the edited meeting and the new one (unchanged ones are skipped)
    out = kg.query(EXEC, "뉴로엣지")
    by_ref = {d["ref"]: d for d in out["decisions"]}
    new = next(r for r in by_ref if r.startswith(saved["meeting_id"]))
    assert by_ref["MTG-2026-09-15-D1"]["status"] == "superseded" and by_ref["MTG-2026-09-15-D1"]["superseded_by"] == new
    assert by_ref[new]["status"] == "active" and by_ref[new]["about_entity"]
    assert by_ref[new]["premises"][0]["statement"] == "알파모바일 공급계약이 유지된다"
    with kg.connect() as conn:
        assigned = conn.execute("""SELECT f.status, f.valid_to FROM kg_facts f WHERE f.subject = 'action_item:MTG-2026-09-15-A3'
                                   AND f.predicate = 'ASSIGNED_TO'""").fetchone()
    assert assigned["status"] == "ended" and assigned["valid_to"] is not None


def _judge_supply_contract(meeting_code: str) -> None:
    articles = json.loads((DATA_DIR / "news.json").read_text(encoding="utf-8"))["articles"]
    ended = next(a for a in articles if a["id"] == "NEWS-2026-11-17-01")

    def ask(system, user):
        return json.dumps({"effect": "broken", "reason": "알파모바일이 공급계약 중단을 발표했다"}, ensure_ascii=False)

    with MEETING_STORE.connect() as conn:
        out = decision_watch.scan_articles(conn, [ended], ask)
    assert out["shaken"] == 1


def test_assessed_influence_is_apart_from_associations(kg):
    saved = save_minutes(
        store=MEETING_STORE, viewer=EXEC, title="뉴로엣지 인수 가격 재검토", date="2026-10-20", topics=["뉴로엣지"],
        summary="가격 상한 결정", classification=3, finalize=True,
        attendees=[{"name": "김민수", "title": "CEO", "role": "chair"}],
        decisions=[{"kind": "criterion", "statement": "뉴로엣지 인수가격 상한 2.3조 원", "speaker": "김민수 (CEO)",
                    "premises": [{"kind": "premise", "statement": "알파모바일 공급계약이 유지된다", "speaker": "김민수 (CEO)",
                                  "watch": ["알파모바일", "공급계약"]}]}])
    assert saved.get("finalized"), saved.get("warnings")
    _judge_supply_contract(saved["meeting_id"])
    kg.sync(Extractor())
    out = kg.query(EXEC, "뉴로엣지")
    [impact] = out["assessed_impacts"]
    ref = f"{saved['meeting_id']}-D1"
    assert impact["effect"] == "broken" and impact["judged"] == "model" and impact["decision"]["ref"] == ref
    assert impact["news"]["id"] == "NEWS-2026-11-17-01" and impact["premise"]["status"] == "broken"
    # the judged pair is not repeated as an association; other pairs stay associations
    supply_event = next(e["id"] for e in out["news_events"] if SUPPLY_EVENT in e["title"])
    pairs = {(a["news_event"]["id"], a["decision"]["ref"]) for a in out["associations"]}
    assert (supply_event, ref) not in pairs and pairs


def test_what_a_user_may_not_see_stays_unseen(kg):
    kg.sync(Extractor())
    out = kg.query(STAFF, "뉴로엣지")  # MTG-2026-09-15 is 대외비 (3); staff has clearance 2
    assert out["found"] and out["meetings"] == [] and out["decisions"] == [] and out["follow_ups"] == []
    assert out["associations"] == [] and out["assessed_impacts"] == []
    assert out["reported_facts"]  # news is public, as in the news Tools
    # and the rule follows the meeting store: allowed_groups too, read when asked (no copy to go stale)
    with MEETING_STORE.connect() as conn:
        conn.execute("UPDATE meetings SET allowed_groups = ARRAY['board'] WHERE code = 'MTG-2026-09-15'")
    assert kg.query(EXEC, "뉴로엣지")["decisions"] == []
    board = Viewer("chair@example.com", "의장", frozenset({"board"}), 4, True)
    assert kg.query(board, "뉴로엣지")["decisions"]


def test_each_system_has_its_own_graph(kg):
    kg.sync(Extractor())
    GROUP_KG.sync(Extractor())
    k = kg.query(EXEC, "뉴로엣지")
    group = GROUP_KG.query(EXEC, "뉴로엣지")
    assert all(not m["id"].startswith("MTG-G-") for m in k["meetings"])
    assert all(m["id"].startswith("MTG-G-") for m in group.get("meetings", []))
    assert not any(f["subject"] == "알파모바일" or f["object"] == "알파모바일" for f in group.get("reported_facts", []))
    with psycopg.connect(MEETING_STORE._dsn or __import__("os").environ["MEETINGS_DATABASE_URL"]) as conn:
        k_nodes = conn.execute("SELECT count(*) FROM kelec.kg_nodes").fetchone()[0]
        g_nodes = conn.execute("SELECT count(*) FROM grp.kg_nodes").fetchone()[0]
    assert k_nodes and g_nodes


class _MergeEvents:
    """A different resolver: reported relations of the same parties are one fact whatever the event."""

    VERSION = "test-merge"
    resolve_entities = staticmethod(resolve.resolve_entities)
    normalize_predicate = staticmethod(resolve.normalize_predicate)
    reconcile = staticmethod(resolve.reconcile)

    @staticmethod
    def instance_key(claim):
        return ""


def test_another_resolver_re_resolves_without_the_model(kg):
    model = Extractor()
    kg.sync(model)
    calls = model.calls
    kg.rebuild(_MergeEvents)
    assert len(_facts("SUES")) == 1 and _facts("SUES")[0]["resolver"] == "test-merge"
    kg.rebuild()
    assert len(_facts("SUES")) == 2 and model.calls == calls


def test_unreadable_extraction_waits_for_the_next_sync(kg):
    def broken(system, user):
        return "모르겠습니다"

    first = kg.sync(broken)
    assert first["llm_failed"] > 0 and first["pending_extraction"] > 0
    assert _facts("SUPPLIES_TO") == []
    second = kg.sync(Extractor())
    assert second["pending_extraction"] == 0 and len(_facts("SUPPLIES_TO")) == 1


# ---------------------------------------------------------------------------
# The Advisory Agent reads the graph and carries it into its review
# ---------------------------------------------------------------------------


class _Model:
    def __init__(self, turns):
        self.turns = list(turns)
        self.seen = []

    def bind_tools(self, _tools):
        return self

    def stream(self, messages):
        self.seen.append(list(messages))
        turn = self.turns.pop(0)
        if isinstance(turn, AIMessage):
            yield turn
            return
        yield AIMessageChunk(content=turn)


def test_advisory_reads_the_graph_and_carries_it_into_its_review(kg, monkeypatch):
    from app.systems.k_electronics.agents import advisory

    kg.sync(Extractor())
    context = "KG: 알파모바일 공급계약 종료(2026-11-17, 반도체경제) · MTG-2026-09-15-D1 2.5조 원 초과 시 가격 재검토"
    model = _Model([
        AIMessage(content="", tool_calls=[{"name": "query_enterprise_kg", "args": {"query": "뉴로엣지 인수 검토", "entities": ["뉴로엣지"]}, "id": "k1"}]),
        AIMessage(content="", tool_calls=[{"name": "run_mna_review", "args": {"query": "뉴로엣지 인수 검토", "company": "뉴로엣지",
                                                                               "persona": "CFO", "context": context}, "id": "r1"}]),
        "권고: 조건부 진행 (최대 고객 공급계약 종료 반영)",
    ])
    monkeypatch.setattr(agent_core, "get_chat_model", lambda: model)
    _, events = collect_events(lambda: advisory.run_advisory_agent("뉴로엣지 인수 타당성을 검토해줘", invocation="delegation"))
    tools = [(e["type"], e["tool"]) for e in events if e["type"] in ("tool_start", "tool_end")]
    assert tools == [("tool_start", "query_enterprise_kg"), ("tool_end", "query_enterprise_kg"),
                     ("tool_start", "run_mna_review"), ("tool_end", "run_mna_review")]
    kg_result = next(e["result"] for e in events if e["type"] == "tool_end" and e["tool"] == "query_enterprise_kg")
    assert kg_result["found"] and any(f["predicate"] == "SUPPLIES_TO" and f["status"] == "ended" for f in kg_result["reported_facts"])
    review = next(e["result"] for e in events if e["type"] == "tool_end" and e["tool"] == "run_mna_review")
    assert review["found"] and review["context_signals"]  # the KG's criterion reached the workflow's signals
    # the KG result reached the model as a tool message before it wrote the review call
    assert any("SUPPLIES_TO" in getattr(m, "content", "") for m in model.seen[1])
