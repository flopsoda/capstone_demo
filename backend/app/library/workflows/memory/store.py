"""MemoryStore — the conversation memory's tables in one agent system's schema (next to its meeting records), and the
two searches: by meaning (pgvector cosine over one model's vectors) and by words (pg_trgm). Every read is one user's.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ..meeting_store import MeetingStore, StoreUnavailable
from ..news.store import _vector

SQL_DIR = Path(__file__).with_name("sql")


class MemoryStore:
    def __init__(self, meeting_store: MeetingStore):
        self.meeting_store = meeting_store
        self._ready = False
        self._lock = threading.Lock()

    @property
    def schema(self) -> str:
        return self.meeting_store.schema

    def reset(self) -> None:
        self._ready = False

    def migrate(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            with self.meeting_store.connect() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{self.schema}:memory",))
                if not conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone():
                    try:
                        with conn.transaction():
                            conn.execute("CREATE EXTENSION vector WITH SCHEMA public")
                    except psycopg.Error as e:
                        raise StoreUnavailable(f"대화 기억 저장소에 pgvector 확장이 없습니다: {e}") from e
                conn.execute("""CREATE TABLE IF NOT EXISTS mem_schema_version (version integer PRIMARY KEY, name text NOT NULL,
                                applied_at timestamptz NOT NULL DEFAULT now())""")
                done = {r["version"] for r in conn.execute("SELECT version FROM mem_schema_version")}
                for path in sorted(SQL_DIR.glob("[0-9][0-9][0-9]_*.sql")):
                    if int(path.name[:3]) not in done:
                        conn.execute(path.read_text(encoding="utf-8"))
                        conn.execute("INSERT INTO mem_schema_version (version, name) VALUES (%s, %s)", (int(path.name[:3]), path.name))
            self._ready = True

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        self.migrate()
        with self.meeting_store.connect() as conn:
            yield conn

    # ── writing ──────────────────────────────────────────────────────────────

    def save_turn(self, *, run_id: str, agent: str, user_key: str, user_text: str, response: str,
                  handoffs: list[dict[str, Any]], chunks: list[str]) -> list[tuple[int, str]]:
        """The turn and its chunks, once per run. Returns (chunk id, text) to embed — none when the run was kept already."""
        with self.connect() as conn:
            row = conn.execute(
                """INSERT INTO mem_turns (run_id, agent, user_key, user_text, response, handoffs) VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (run_id) DO NOTHING RETURNING id""",
                (run_id, agent, user_key, user_text, response, Jsonb(handoffs))).fetchone()
            if row is None:
                return []
            return [(conn.execute("INSERT INTO mem_chunks (turn_id, seq, text) VALUES (%s, %s, %s) RETURNING id",
                                  (row["id"], seq, text)).fetchone()["id"], text) for seq, text in enumerate(chunks)]

    def save_embeddings(self, model: str, rows: Iterable[tuple[int, list[float]]]) -> int:
        n = 0
        with self.connect() as conn:
            for chunk_id, vector in rows:
                conn.execute("""INSERT INTO mem_embeddings (chunk_id, model, embedding) VALUES (%s, %s, %s::vector)
                                ON CONFLICT (chunk_id, model) DO UPDATE SET embedding = EXCLUDED.embedding, embedded_at = now()""",
                             (chunk_id, model, _vector(vector)))
                n += 1
        return n

    def unembedded(self, model: str, limit: int = 500) -> list[dict[str, Any]]:
        """Chunks without a vector from `model` (kept before the model was set, or when it was down)."""
        with self.connect() as conn:
            return list(conn.execute(
                """SELECT c.id, c.text FROM mem_chunks c LEFT JOIN mem_embeddings e ON e.chunk_id = c.id AND e.model = %s
                   WHERE e.chunk_id IS NULL ORDER BY c.id LIMIT %s""", (model, limit)))

    # ── searching (one user's turns only) ────────────────────────────────────

    _TURN = """t.id AS turn_id, t.created_at, t.user_text, t.response, t.handoffs"""

    def by_meaning(self, user_key: str, model: str, vector: list[float], *, limit: int, floor: float) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return list(conn.execute(
                f"""SELECT {self._TURN}, 1 - (e.embedding <=> %(v)s::vector) AS similarity
                    FROM mem_embeddings e JOIN mem_chunks c ON c.id = e.chunk_id JOIN mem_turns t ON t.id = c.turn_id
                    WHERE t.user_key = %(user)s AND e.model = %(model)s AND 1 - (e.embedding <=> %(v)s::vector) >= %(floor)s
                    ORDER BY e.embedding <=> %(v)s::vector LIMIT %(limit)s""",
                {"v": _vector(vector), "user": user_key, "model": model, "floor": floor, "limit": limit}))

    def by_words(self, user_key: str, query: str, *, limit: int, floor: float) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return list(conn.execute(
                f"""SELECT {self._TURN}, word_similarity(%(q)s, c.text) AS similarity
                    FROM mem_chunks c JOIN mem_turns t ON t.id = c.turn_id
                    WHERE t.user_key = %(user)s AND word_similarity(%(q)s, c.text) >= %(floor)s
                    ORDER BY similarity DESC, t.created_at DESC LIMIT %(limit)s""",
                {"q": query, "user": user_key, "floor": floor, "limit": limit}))
