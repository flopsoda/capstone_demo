"""KGStore — one agent system's Enterprise KG: its tables, keeping them in step with the sources, and reading them.

The tables live in the system's own schema, next to its meeting records (the deployment's DB roles may not create
schemas; and a fact from a meeting is filtered by that meeting's access rule). The news comes from the system's
news store. A system binds its stores and its data file of companies:

    KGStore(MEETING_STORE, NEWS_STORE, aliases=DATA_DIR / "companies.json")

    sync(ask)        brings the graph up to date: changed sources → raw layer, then the resolved layer is rebuilt.
                     Repeated until the alias index is stable, so a second run with the same data writes nothing.
    rebuild(resolver) re-resolves the whole raw layer (no model calls): how a new resolver is tried
    query(viewer, …) the Advisory Agent's view (query.py)

Seeds of the resolver (entities that exist before any text is read): the companies of the data file (name and
aliases) and the people and units of the meeting directory.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ..meeting_store import MeetingStore, Viewer
from ..news.store import NewsStore
from . import project, resolve
from .project import Ask, Budget

SQL_DIR = Path(__file__).with_name("sql")
MAX_ROUNDS = 3
DEFAULT_LLM_BUDGET = 30  # extraction calls per sync (KG_LLM_BUDGET); the rest wait for the next sync


def llm_budget() -> int:
    try:
        return max(0, int(os.environ.get("KG_LLM_BUDGET", "") or DEFAULT_LLM_BUDGET))
    except ValueError:
        return DEFAULT_LLM_BUDGET


class KGStore:
    def __init__(self, meeting_store: MeetingStore, news_store: NewsStore | None = None, *, aliases: Path | None = None):
        self.meeting_store = meeting_store
        self.news_store = news_store
        self.aliases = aliases
        self._ready = False
        self._lock = threading.Lock()

    @property
    def schema(self) -> str:
        return self.meeting_store.schema

    # ── tables ───────────────────────────────────────────────────────────────

    def reset(self) -> None:
        self._ready = False

    def migrate(self) -> list[int]:
        if self._ready:
            return []
        with self._lock:
            if self._ready:
                return []
            applied = []
            with self.meeting_store.connect() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{self.schema}:kg",))
                conn.execute("""CREATE TABLE IF NOT EXISTS kg_schema_version (version integer PRIMARY KEY, name text NOT NULL,
                                applied_at timestamptz NOT NULL DEFAULT now())""")
                done = {r["version"] for r in conn.execute("SELECT version FROM kg_schema_version")}
                for path in sorted(SQL_DIR.glob("[0-9][0-9][0-9]_*.sql")):
                    version = int(path.name[:3])
                    if version not in done:
                        conn.execute(path.read_text(encoding="utf-8"))
                        conn.execute("INSERT INTO kg_schema_version (version, name) VALUES (%s, %s)", (version, path.name))
                        applied.append(version)
            self._ready = True
            return applied

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        self.migrate()
        with self.meeting_store.connect() as conn:
            yield conn

    # ── resolved layer ───────────────────────────────────────────────────────

    def seeds(self, conn: psycopg.Connection) -> list[resolve.Seed]:
        out = []
        if self.aliases and self.aliases.exists():
            for c in json.loads(self.aliases.read_text(encoding="utf-8")).get("companies", []):
                out.append(resolve.Seed(f"org:{resolve.normalize(c['name'])}", "organization", c["name"], tuple(c.get("aliases") or ())))
        for p in conn.execute("""SELECT id, display_name, title FROM people WHERE speaker_meeting_id IS NULL
                                 AND display_name !~ '^(화자 [A-Z]{1,2}|미확인 화자( [0-9]+)?)$' ORDER BY id"""):
            out.append(resolve.Seed(f"person:{p['id']}", "person", p["display_name"],
                                    (f"{p['display_name']} ({p['title']})",) if p["title"] else ()))
        for u in conn.execute("SELECT id, name FROM org_units ORDER BY id"):
            out.append(resolve.Seed(f"unit:{u['id']}", "org_unit", u["name"]))
        return out

    def rebuild(self, resolver: Any = resolve) -> dict[str, int]:
        """Raw layer → entities, links and facts, as a whole, in one transaction."""
        with self.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{self.schema}:kg-rebuild",))
            return self._rebuild(conn, resolver)

    def _rebuild(self, conn: psycopg.Connection, resolver: Any) -> dict[str, int]:
        mentions = conn.execute("SELECT id, surface, aliases, type_guess FROM kg_mentions").fetchall()
        entities = resolver.resolve_entities(mentions, self.seeds(conn))
        records = {r["id"] for r in conn.execute("SELECT id FROM kg_nodes WHERE kind = 'record'")}

        def endpoint(key: str) -> str | None:
            if key.startswith("mention:"):
                link = entities.links.get(key[len("mention:"):])
                return link[0] if link else None
            return key if key in records or key in entities.nodes else None

        facts = resolve.build_facts(conn.execute("SELECT * FROM kg_claims").fetchall(), endpoint, resolver)
        version = getattr(resolver, "VERSION", "custom")
        conn.execute("DELETE FROM kg_facts")
        conn.execute("DELETE FROM kg_mention_links")
        conn.execute("DELETE FROM kg_nodes WHERE kind = 'entity'")
        for node_id, n in entities.nodes.items():
            aliases = sorted(n["aliases"])
            conn.execute("INSERT INTO kg_nodes (id, kind, type, label, attrs, resolver) VALUES (%s, 'entity', %s, %s, %s, %s)",
                         (node_id, n["type"], n["label"], Jsonb({"aliases": aliases}), version))
            for alias in aliases:
                conn.execute("INSERT INTO kg_aliases (alias_norm, node_id, alias) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                             (resolve.normalize(alias), node_id, alias))
        for mention_id, (node_id, method) in entities.links.items():
            conn.execute("INSERT INTO kg_mention_links (mention_id, node_id, method, resolver) VALUES (%s, %s, %s, %s)",
                         (mention_id, node_id, method, version))
        for f in facts:
            conn.execute(
                """INSERT INTO kg_facts (id, subject, predicate, object, instance_key, fact_class, status, valid_from, valid_to,
                                         first_seen, last_seen, attrs, resolver)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (f["id"], f["subject"], f["predicate"], f["object"], f["instance_key"], f["fact_class"], f["status"],
                 f["valid_from"], f["valid_to"], f["first_seen"], f["last_seen"], Jsonb(f["attrs"]), version))
            for claim_id in f["claims"]:
                conn.execute("INSERT INTO kg_fact_support (fact_id, claim_id) VALUES (%s, %s)", (f["id"], claim_id))
        # Records no claim names any more (an event that left the scope, a deleted action item) go.
        conn.execute("""DELETE FROM kg_nodes n WHERE n.kind = 'record'
                        AND NOT EXISTS (SELECT 1 FROM kg_claims c WHERE c.subject = n.id OR c.object = n.id)""")
        return {"entities": len(entities.nodes), "facts": len(facts), "links": len(entities.links)}

    # ── keeping up with the sources ──────────────────────────────────────────

    def _anchors(self, conn: psycopg.Connection) -> list[str]:
        """Names that bring a news event into the graph: the system's companies and organizations its meetings discuss."""
        rows = conn.execute(
            """SELECT DISTINCT a.alias FROM kg_aliases a JOIN kg_nodes n ON n.id = a.node_id
               WHERE n.type = 'organization' AND (n.id LIKE 'org:%%' AND EXISTS (
                     SELECT 1 FROM kg_facts f WHERE f.object = n.id AND f.predicate = 'CONCERNS')
                  OR n.id = ANY(%s))""", ([s.id for s in self.seeds(conn) if s.type == "organization"],)).fetchall()
        return sorted(r["alias"] for r in rows)

    def sync(self, ask: Ask | None = None, *, budget: int | None = None, now: datetime | None = None) -> dict[str, Any]:
        """Brings the graph up to date with the meeting store and the news store. `ask(system, user) -> text` is the
        model for news extraction (None: structural only; events wait for a model). Returns what it did."""
        from ..news.pipeline import lookback_days

        budget_ = Budget(llm_budget() if budget is None else budget)
        stats: dict[str, Any] = {"rounds": 0}
        meetings, news_events, extracted = set(), set(), set()  # sources that changed (a source may change in two rounds)
        since = (now or datetime.now(timezone.utc)) - timedelta(days=lookback_days())
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM kg_nodes WHERE kind = 'entity' LIMIT 1").fetchone():
                self._rebuild(conn, resolve)  # the seeds, so the first pass can spot names
        for _ in range(MAX_ROUNDS):
            stats["rounds"] += 1
            changed = 0
            with self.connect() as conn:
                index = project.alias_index(conn)
                fp = project.fingerprint(index)
                anchors = self._anchors(conn)
            if self.news_store is not None and self.news_store.configured():
                with self.news_store.connect() as news:
                    events = [e for e in (project.load_event(news, i) for i in project.scoped_events(news, anchors, since)) if e]
                for data in events:
                    with self.connect() as conn:
                        result = project.project_news_event(conn, data, index, fp, ask, budget_)
                    if result != "unchanged":
                        changed += 1
                        news_events.add(data["event"]["id"])
                        if result == "extracted":
                            extracted.add(data["event"]["id"])
            with self.connect() as conn:
                live = project.final_meetings(conn)
                for m in live:
                    if project.project_meeting(conn, m["id"], index, fp):
                        changed += 1
                        meetings.add(m["code"])
                gone = conn.execute("SELECT source_ref FROM kg_sources WHERE source_kind = 'meeting' AND NOT source_ref = ANY(%s)",
                                    ([m["code"] for m in live],)).fetchall()
                for g in gone:  # a deleted meeting: its claims went with it (ON DELETE CASCADE); forget the source
                    conn.execute("DELETE FROM kg_claims WHERE source_kind = 'meeting' AND source_ref = %s", (g["source_ref"],))
                    conn.execute("DELETE FROM kg_sources WHERE source_kind = 'meeting' AND source_ref = %s", (g["source_ref"],))
                    changed += 1
            if not changed:
                break
            with self.connect() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{self.schema}:kg-rebuild",))
                stats.update(self._rebuild(conn, resolve))
        stats.update({"meetings": len(meetings), "news_events": len(news_events), "extracted": len(extracted),
                      "llm_calls": budget_.used, "llm_failed": budget_.failed})
        with self.connect() as conn:
            stats["pending_extraction"] = conn.execute(
                "SELECT count(*) AS n FROM kg_sources WHERE source_kind = 'news_event' AND source_hash LIKE '%%|-'").fetchone()["n"]
        return stats

    def watch_pass(self, ask: Ask | None) -> dict[str, Any]:
        """One background pass (jobs.py), skipped when another process is syncing this graph."""
        with self.connect() as conn:
            if not conn.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS ok", (f"{self.schema}:kg-sync",)).fetchone()["ok"]:
                return {}
            try:
                return self.sync(ask)
            finally:
                conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"{self.schema}:kg-sync",))

    # ── reading ──────────────────────────────────────────────────────────────

    def query(self, viewer: Viewer, query: str, entities: list[str] | None = None) -> dict[str, Any]:
        from .query import query_graph

        with self.connect() as conn:
            return query_graph(conn, viewer, query, entities)
