"""News store — the Postgres database behind the news pipeline, one schema per agent system
(K Electronics `kelec_news`, Group `grp_news`), like the meeting store.

The connection string is NEWS_DATABASE_URL, or MEETINGS_DATABASE_URL when that is not set (one
Postgres, separate schemas). The schema is versioned: sql/NNN_*.sql run once each, in order,
recorded in schema_version (store.migrate(), done on first use). A system may bind a data file
of articles it already has (NewsStore(schema, seed=data/news.json), seed.py): it is applied after
the migrations, once per process, and upserts the same rows every time. Those articles are then
news like any other: every request searches, groups and analyzes everything stored the same way.

Search: search(terms) — keywords (text or linked names); semantic_search(vector, model) — meaning
(pgvector cosine over news_article_embeddings, needs the `vector` extension on the server);
pipeline.gather() fuses the two.

Read interface for other components (e.g. the Enterprise KG) — stable ids, no LLM, no network:
    get_article(id) · get_articles(ids) · get_event(id) · events_for(surface | entity_ref) · provenance(id)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..meeting_store import StoreUnavailable

SQL_DIR = Path(__file__).with_name("sql")
LEVEL_RANK = {"title": 0, "snippet": 1, "partial": 2, "full": 3}


def article_id(url: str) -> str:
    """Stable id of an article: the same normalized URL is the same id in any schema or database."""
    return "art_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]


def refresh_events(conn: psycopg.Connection, event_ids: list[str]) -> None:
    """Events' first and last publication from their articles."""
    if event_ids:
        conn.execute(
            """UPDATE news_events e SET first_published = s.first, last_published = s.last, updated_at = now()
               FROM (SELECT ea.event_id, min(a.published_at) AS first, max(a.published_at) AS last
                     FROM news_event_articles ea JOIN news_articles a ON a.id = ea.article_id
                     WHERE ea.event_id = ANY(%s::uuid[]) GROUP BY ea.event_id) s
               WHERE e.id = s.event_id""", (event_ids,))


def migrations() -> list[tuple[int, Path]]:
    out = []
    for p in sorted(SQL_DIR.glob("*.sql")):
        m = re.match(r"(\d+)_", p.name)
        if m:
            out.append((int(m.group(1)), p))
    return out


class NewsStore:
    def __init__(self, schema: str, *, dsn: str | None = None, seed: Path | None = None):
        self.schema = schema
        self._dsn = dsn
        self.seed = seed
        self._ready = False
        self._lock = threading.Lock()

    # ── connection and schema ────────────────────────────────────────────────

    def dsn(self) -> str:
        return self._dsn or os.environ.get("NEWS_DATABASE_URL", "") or os.environ.get("MEETINGS_DATABASE_URL", "")

    def configured(self) -> bool:
        return bool(self.dsn())

    def _connect_raw(self) -> psycopg.Connection:
        dsn = self.dsn()
        if not dsn:
            raise StoreUnavailable("뉴스 저장소(NEWS_DATABASE_URL 또는 MEETINGS_DATABASE_URL)가 설정되지 않았습니다.")
        try:
            conn = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5)
        except psycopg.OperationalError as e:
            raise StoreUnavailable(f"뉴스 저장소에 연결할 수 없습니다: {e}") from e
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema)))
        return conn

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        """One transaction on this system's news schema: committed when the block ends."""
        self.migrate()
        with self._connect_raw() as conn:
            yield conn

    def reset(self) -> None:
        self._ready = False

    def migrate(self) -> list[int]:
        """Creates the schema when missing and applies the SQL files it has not run yet. Returns them."""
        if self._ready:
            return []
        with self._lock:
            if self._ready:
                return []
            applied: list[int] = []
            with self._connect_raw() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (self.schema,))
                if not conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'").fetchone():
                    conn.execute("CREATE EXTENSION pg_trgm WITH SCHEMA public")
                if not conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone():
                    try:
                        with conn.transaction():
                            conn.execute("CREATE EXTENSION vector WITH SCHEMA public")
                    except psycopg.Error as e:
                        raise StoreUnavailable("뉴스 저장소에 pgvector 확장이 없습니다 (Postgres 서버에 pgvector 설치 필요, "
                                               f"README 참고): {e}") from e
                if not conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (self.schema,)).fetchone():
                    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
                conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version integer PRIMARY KEY, "
                             "name text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
                done = {r["version"] for r in conn.execute("SELECT version FROM schema_version")}
                for version, path in migrations():
                    if version in done:
                        continue
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute("INSERT INTO schema_version (version, name) VALUES (%s, %s)", (version, path.name))
                    applied.append(version)
                if self.seed is not None and self.seed.exists():
                    from .seed import apply_seed  # seed.py imports this module

                    apply_seed(conn, self.schema, json.loads(self.seed.read_text(encoding="utf-8")))
            self._ready = True
            return applied

    # ── writes used by the pipeline ──────────────────────────────────────────

    def feeds_due(self, urls: list[str], ttl_minutes: float, now: datetime) -> set[str]:
        """Feeds not polled within the TTL (never polled = due)."""
        with self.connect() as conn:
            recent = {r["url"] for r in conn.execute(
                "SELECT url FROM news_feeds WHERE url = ANY(%s) AND last_polled_at > %s - make_interval(secs => %s)",
                (urls, now, ttl_minutes * 60))}
        return set(urls) - recent

    def record_feed(self, conn: psycopg.Connection, *, url: str, domain: str, outlet: str, kind: str,
                    status: str, count: int | None, now: datetime) -> None:
        conn.execute("INSERT INTO news_outlets (domain, name) VALUES (%s, %s) ON CONFLICT (domain) DO NOTHING", (domain, outlet))
        conn.execute(
            """INSERT INTO news_feeds (url, outlet_domain, kind, last_polled_at, last_status, last_item_count)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (url) DO UPDATE SET last_polled_at = EXCLUDED.last_polled_at,
                   last_status = EXCLUDED.last_status, last_item_count = EXCLUDED.last_item_count""",
            (url, domain, kind, now, status, count))

    def add_discovered(self, conn: psycopg.Connection, rows: Iterable[dict[str, Any]]) -> int:
        """Feed items → articles (feed data only, fetch_status pending). Known URLs are left alone. Returns new ones."""
        added = 0
        for r in rows:
            conn.execute("INSERT INTO news_outlets (domain, name) VALUES (%s, %s) ON CONFLICT (domain) DO NOTHING",
                         (r["domain"], r["outlet"]))
            cur = conn.execute(
                """INSERT INTO news_articles (id, url, outlet_domain, title, summary, content_level, published_at, feed_url, discovered_at)
                   VALUES (%(id)s, %(url)s, %(domain)s, %(title)s, %(summary)s, %(content_level)s, %(published)s, %(feed_url)s, %(discovered)s)
                   ON CONFLICT DO NOTHING""", r)
            added += cur.rowcount
        return added

    def pending(self, *, terms: list[str] | None, since: datetime | None, limit: int) -> list[dict[str, Any]]:
        """Articles whose page was not fetched yet, newest first; with `terms`, only those whose
        feed title/description name every term."""
        where, params = ["fetch_status = 'pending'"], {"limit": limit}
        if since is not None:
            where.append("(published_at IS NULL OR published_at >= %(since)s)")
            params["since"] = since
        for i, t in enumerate(terms or []):
            where.append(f"{_squeezed('title', 'summary')} LIKE %(t{i})s")
            params[f"t{i}"] = f"%{_like(squeeze(t))}%"
        with self.connect() as conn:
            return list(conn.execute(
                f"SELECT id, url, title, summary FROM news_articles WHERE {' AND '.join(where)} "
                "ORDER BY published_at DESC NULLS LAST LIMIT %(limit)s", params))

    def save_fetch(self, article_id_: str, *, status: str, now: datetime, content_level: str | None = None,
                   body: str = "", body_hash: str | None = None, title: str | None = None, summary: str | None = None,
                   canonical_url: str | None = None, published: datetime | None = None, usage_notice: bool = False) -> None:
        """Result of fetching one page. A better content level replaces the feed data; a failure only records the status."""
        with self.connect() as conn:
            if content_level is None:
                conn.execute("UPDATE news_articles SET fetch_status = %s, fetched_at = %s WHERE id = %s",
                             (status, now, article_id_))
                return
            conn.execute(
                """UPDATE news_articles SET fetch_status = %(status)s, fetched_at = %(now)s,
                       content_level = CASE WHEN %(rank)s >= (CASE content_level WHEN 'full' THEN 3 WHEN 'partial' THEN 2
                                                 WHEN 'snippet' THEN 1 ELSE 0 END) THEN %(level)s ELSE content_level END,
                       body = CASE WHEN %(body)s <> '' THEN %(body)s ELSE body END,
                       body_hash = COALESCE(%(hash)s, body_hash),
                       title = COALESCE(NULLIF(title, ''), %(title)s),
                       summary = CASE WHEN length(coalesce(%(summary)s, '')) > length(summary) THEN %(summary)s ELSE summary END,
                       canonical_url = COALESCE(%(canonical)s, canonical_url),
                       published_at = COALESCE(published_at, %(published)s),
                       usage_notice = %(notice)s
                   WHERE id = %(id)s""",
                {"status": status, "now": now, "rank": LEVEL_RANK[content_level], "level": content_level, "body": body,
                 "hash": body_hash, "title": title, "summary": summary, "canonical": canonical_url,
                 "published": published, "notice": usage_notice, "id": article_id_})

    def record_run(self, *, trigger: str, query: str | None, started: datetime, finished: datetime, stats: dict) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO news_ingest_runs (trigger, query, started_at, finished_at, stats) VALUES (%s, %s, %s, %s, %s)",
                         (trigger, query, started, finished, Jsonb(stats)))

    # ── search and reuse ─────────────────────────────────────────────────────

    def search(self, terms: list[str], *, since: datetime, until: datetime | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """Stored articles that name every term — in the title, description or body (spacing and joining
        marks ignored), or as a name the article is linked to (news_mentions: a company it is about) —
        published in the window, newest first."""
        if not terms:
            return []
        where = ["published_at >= %(since)s"]
        params: dict[str, Any] = {"since": since, "limit": limit}
        if until is not None:
            where.append("published_at <= %(until)s")
            params["until"] = until
        for i, t in enumerate(terms):
            where.append(f"({_squeezed('a.title', 'a.summary', 'a.body')} LIKE %(t{i})s OR EXISTS (SELECT 1 FROM news_mentions m "
                         f"WHERE m.article_id = a.id AND {_squeezed('m.surface')} = %(s{i})s))")
            params[f"t{i}"] = f"%{_like(squeeze(t))}%"
            params[f"s{i}"] = squeeze(t)
        with self.connect() as conn:
            return list(conn.execute(
                f"""SELECT a.*, o.name AS outlet, ea.event_id
                    FROM news_articles a JOIN news_outlets o ON o.domain = a.outlet_domain
                    LEFT JOIN news_event_articles ea ON ea.article_id = a.id
                    WHERE {' AND '.join(where)} ORDER BY a.published_at DESC LIMIT %(limit)s""", params))

    def semantic_search(self, vector: list[float], model: str, *, since: datetime, until: datetime | None = None,
                        limit: int = 30, floor: float = 0.0) -> list[dict[str, Any]]:
        """Stored articles closest in meaning to `vector` (cosine similarity ≥ floor, `similarity` on each row),
        published in the window, most similar first. Only articles embedded with `model` take part."""
        with self.connect() as conn:
            return list(conn.execute(
                """SELECT a.*, o.name AS outlet, ea.event_id, 1 - (e.embedding <=> %(v)s::vector) AS similarity
                   FROM news_article_embeddings e JOIN news_articles a ON a.id = e.article_id
                   JOIN news_outlets o ON o.domain = a.outlet_domain
                   LEFT JOIN news_event_articles ea ON ea.article_id = a.id
                   WHERE e.model = %(model)s AND a.published_at >= %(since)s
                     AND (%(until)s::timestamptz IS NULL OR a.published_at <= %(until)s)
                     AND 1 - (e.embedding <=> %(v)s::vector) >= %(floor)s
                   ORDER BY e.embedding <=> %(v)s::vector LIMIT %(limit)s""",
                {"v": _vector(vector), "model": model, "since": since, "until": until, "floor": floor, "limit": limit}))

    def pending_similar(self, vector: list[float], model: str, *, since: datetime, limit: int, floor: float) -> list[dict[str, Any]]:
        """Articles whose page was not fetched yet, closest in meaning to `vector` (their feed text was embedded)."""
        with self.connect() as conn:
            return list(conn.execute(
                """SELECT a.id, a.url, a.title, a.summary FROM news_article_embeddings e JOIN news_articles a ON a.id = e.article_id
                   WHERE e.model = %(model)s AND a.fetch_status = 'pending' AND a.url IS NOT NULL
                     AND (a.published_at IS NULL OR a.published_at >= %(since)s)
                     AND 1 - (e.embedding <=> %(v)s::vector) >= %(floor)s
                   ORDER BY e.embedding <=> %(v)s::vector LIMIT %(limit)s""",
                {"v": _vector(vector), "model": model, "since": since, "floor": floor, "limit": limit}))

    def unembedded(self, model: str, *, since: datetime | None, limit: int) -> list[dict[str, Any]]:
        """Articles without a vector from `model`, or whose text got better since (content level), newest first."""
        with self.connect() as conn:
            return list(conn.execute(
                """SELECT a.id, a.title, a.summary, a.body, a.usage_notice, a.content_level
                   FROM news_articles a LEFT JOIN news_article_embeddings e ON e.article_id = a.id AND e.model = %(model)s
                   WHERE (e.article_id IS NULL OR e.content_level <> a.content_level)
                     AND (%(since)s::timestamptz IS NULL OR a.published_at >= %(since)s)
                   ORDER BY a.published_at DESC NULLS LAST LIMIT %(limit)s""",
                {"model": model, "since": since, "limit": limit}))

    def save_embeddings(self, model: str, rows: Iterable[tuple[str, str, list[float]]]) -> int:
        """(article_id, content_level of the embedded text, vector) — replaces the article's vector from this model."""
        n = 0
        with self.connect() as conn:
            for aid, level, vector in rows:
                conn.execute(
                    """INSERT INTO news_article_embeddings (article_id, model, content_level, embedding)
                       VALUES (%s, %s, %s, %s::vector)
                       ON CONFLICT (article_id, model) DO UPDATE SET content_level = EXCLUDED.content_level,
                           embedding = EXCLUDED.embedding, embedded_at = now()""", (aid, model, level, _vector(vector)))
                n += 1
        return n

    def save_mentions(self, rows: Iterable[tuple[str, str, int, bool]]) -> None:
        """(article_id, surface, mentions, in_title) — refreshed, entity_ref kept."""
        with self.connect() as conn:
            for aid, surface, n, in_title in rows:
                conn.execute(
                    """INSERT INTO news_mentions (article_id, surface, mentions, in_title) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (article_id, surface) DO UPDATE SET mentions = EXCLUDED.mentions, in_title = EXCLUDED.in_title""",
                    (aid, surface, n, in_title))

    def mark_syndicated(self, pairs: Iterable[tuple[str, str]]) -> None:
        """(copy id, original id): a wire story another outlet ran unchanged."""
        with self.connect() as conn:
            for copy, original in pairs:
                conn.execute("UPDATE news_articles SET syndicated_from = %s WHERE id = %s AND syndicated_from IS NULL",
                             (original, copy))

    # ── events ───────────────────────────────────────────────────────────────

    def event_members(self, event_ids: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
        ids = [str(e) for e in event_ids]
        if not ids:
            return {}
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT ea.event_id::text AS event_id, a.id, a.title, a.body, a.summary, a.published_at, a.outlet_domain
                   FROM news_event_articles ea JOIN news_articles a ON a.id = ea.article_id
                   WHERE ea.event_id = ANY(%s::uuid[]) ORDER BY a.published_at""", (ids,)).fetchall()
        out: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(r["event_id"], []).append(r)
        return out

    def assign_events(self, assignments: dict[str, str | None], titles: dict[str, str]) -> dict[str, str]:
        """article id → existing event id, or None for "start a new event" (its key is then the
        article itself). Already-assigned articles keep their event. Returns article id → event id."""
        out: dict[str, str] = {}
        with self.connect() as conn:
            existing = {r["article_id"]: str(r["event_id"]) for r in conn.execute(
                "SELECT article_id, event_id FROM news_event_articles WHERE article_id = ANY(%s)", (list(assignments),))}
            new_for: dict[str, str] = {}  # seed article → new event id
            for aid, target in assignments.items():
                if aid in existing:
                    out[aid] = existing[aid]
                    continue
                if target is None or target in assignments:  # a new group, keyed by its first article
                    seed = target or aid
                    if seed in existing:
                        out[aid] = existing[seed]
                    else:
                        if seed not in new_for:
                            new_for[seed] = str(uuid.uuid4())
                            conn.execute("INSERT INTO news_events (id, title) VALUES (%s, %s)", (new_for[seed], titles.get(seed, "")))
                        out[aid] = new_for[seed]
                else:
                    out[aid] = target
                conn.execute("INSERT INTO news_event_articles (article_id, event_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                             (aid, out[aid]))
            refresh_events(conn, sorted(set(out.values())))
        return out

    # ── analysis ─────────────────────────────────────────────────────────────

    def article_analyses(self, ids: list[str], target: str, version: str) -> dict[str, dict[str, Any]]:
        with self.connect() as conn:
            return {r["article_id"]: r for r in conn.execute(
                "SELECT * FROM news_article_analysis WHERE article_id = ANY(%s) AND target = %s AND analyzer_version = %s",
                (ids, target, version))}

    def save_article_analyses(self, rows: Iterable[dict[str, Any]]) -> None:
        with self.connect() as conn:
            for r in rows:
                conn.execute(
                    """INSERT INTO news_article_analysis (article_id, target, analyzer_version, stance, article_type,
                           risk_category, risk_level, key_facts, entities, basis, method, details)
                       VALUES (%(article_id)s, %(target)s, %(analyzer_version)s, %(stance)s, %(article_type)s,
                           %(risk_category)s, %(risk_level)s, %(key_facts)s, %(entities)s, %(basis)s, %(method)s, %(details)s)
                       ON CONFLICT (article_id, target, analyzer_version) DO UPDATE SET stance = EXCLUDED.stance,
                           article_type = EXCLUDED.article_type, risk_category = EXCLUDED.risk_category,
                           risk_level = EXCLUDED.risk_level, key_facts = EXCLUDED.key_facts, entities = EXCLUDED.entities,
                           basis = EXCLUDED.basis, method = EXCLUDED.method, details = EXCLUDED.details, analyzed_at = now()""",
                    {**r, "key_facts": Jsonb(r.get("key_facts", [])), "entities": Jsonb(r.get("entities", [])),
                     "details": Jsonb(r.get("details", {}))})

    def save_event_analyses(self, rows: Iterable[dict[str, Any]]) -> None:
        with self.connect() as conn:
            for r in rows:
                conn.execute(
                    """INSERT INTO news_event_analysis (event_id, target, analyzer_version, summary, key_facts, impact,
                           risk_category, risk_level, importance_llm, implication, method, details)
                       VALUES (%(event_id)s, %(target)s, %(analyzer_version)s, %(summary)s, %(key_facts)s, %(impact)s,
                           %(risk_category)s, %(risk_level)s, %(importance_llm)s, %(implication)s, %(method)s, %(details)s)
                       ON CONFLICT (event_id, target, analyzer_version) DO UPDATE SET summary = EXCLUDED.summary,
                           key_facts = EXCLUDED.key_facts, impact = EXCLUDED.impact, risk_category = EXCLUDED.risk_category,
                           risk_level = EXCLUDED.risk_level, importance_llm = EXCLUDED.importance_llm,
                           implication = EXCLUDED.implication, method = EXCLUDED.method, details = EXCLUDED.details,
                           analyzed_at = now()""",
                    {**r, "key_facts": Jsonb(r.get("key_facts", [])), "details": Jsonb(r.get("details", {}))})

    # ── read interface (KG and other readers) ────────────────────────────────

    def get_articles(self, ids: list[str]) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return list(conn.execute(
                """SELECT a.id, a.url, a.canonical_url, a.title, a.summary, a.content_level, a.published_at, a.fetched_at,
                          a.discovered_at, a.outlet_domain, o.name AS outlet, a.syndicated_from, ea.event_id::text AS event_id
                   FROM news_articles a JOIN news_outlets o ON o.domain = a.outlet_domain
                   LEFT JOIN news_event_articles ea ON ea.article_id = a.id WHERE a.id = ANY(%s)""", (ids,)))

    def get_article(self, id_: str) -> dict[str, Any] | None:
        rows = self.get_articles([id_])
        return rows[0] if rows else None

    def provenance(self, id_: str) -> dict[str, Any] | None:
        """Where an article came from and when: source URL, outlet, feed, discovery and fetch time, fetch result."""
        with self.connect() as conn:
            return conn.execute(
                """SELECT a.id, a.url, a.canonical_url, a.outlet_domain, o.name AS outlet, a.feed_url, a.discovered_at,
                          a.fetched_at, a.fetch_status, a.content_level, a.usage_notice, a.syndicated_from
                   FROM news_articles a JOIN news_outlets o ON o.domain = a.outlet_domain WHERE a.id = %s""", (id_,)).fetchone()

    def get_event(self, id_: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            event = conn.execute("SELECT id::text AS id, title, first_published, last_published, created_at, updated_at "
                                 "FROM news_events WHERE id = %s::uuid", (id_,)).fetchone()
            if event is None:
                return None
            event["article_ids"] = [r["article_id"] for r in conn.execute(
                """SELECT ea.article_id FROM news_event_articles ea JOIN news_articles a ON a.id = ea.article_id
                   WHERE ea.event_id = %s::uuid ORDER BY a.published_at""", (id_,))]
            event["analyses"] = list(conn.execute(
                "SELECT target, analyzer_version, summary, key_facts, impact, risk_category, risk_level, implication, method, analyzed_at "
                "FROM news_event_analysis WHERE event_id = %s::uuid ORDER BY analyzed_at DESC", (id_,)))
        return event

    def events_for(self, *, surface: str | None = None, entity_ref: str | None = None,
                   since: datetime | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Events with an article that mentions `surface` (or is linked to `entity_ref`), latest first."""
        if not (surface or entity_ref):
            raise ValueError("surface or entity_ref is required")
        col, value = ("m.entity_ref", entity_ref) if entity_ref else ("m.surface", (surface or "").lower())
        with self.connect() as conn:
            return list(conn.execute(
                f"""SELECT e.id::text AS id, e.title, e.first_published, e.last_published, count(DISTINCT a.id) AS articles
                    FROM news_mentions m JOIN news_event_articles ea ON ea.article_id = m.article_id
                    JOIN news_events e ON e.id = ea.event_id JOIN news_articles a ON a.id = m.article_id
                    WHERE {col} = %s AND (%s::timestamptz IS NULL OR e.last_published >= %s)
                    GROUP BY e.id ORDER BY e.last_published DESC NULLS LAST LIMIT %s""",
                (value, since, since, limit)))


_SQUEEZE = re.compile(r"[\s·ㆍ\-‧]+")


def squeeze(text: str) -> str:
    """Spacing and joining marks do not change a word: "원·달러" = "원달러", "2차 전지" = "2차전지"."""
    return _SQUEEZE.sub("", text.lower())


def _squeezed(*columns: str) -> str:
    joined = " || ' ' || ".join(columns)
    return f"regexp_replace(lower({joined}), '[[:space:]·ㆍ‧-]+', '', 'g')"


def _like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _vector(values: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.7g}" for x in values) + "]"


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
