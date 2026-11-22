"""AdvisoryStore — one agent system's verified Advisory judgements, each with a copy of the evidence it rests on.

The tables live in the system's own schema next to its meeting records (as the Enterprise KG's do: the deployment's
DB roles may not create schemas, and a judgement that draws on a meeting is read under that meeting's access rule).
A reader sees a judgement only if their clearance covers its classification and they can see every meeting it cites.

    save(opinion, evidence, viewer=…)   a checked judgement → its code (ADV-YYYY-MM-DD-NN)
    get(viewer, code) / latest(viewer, company) / history(viewer, company)
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ..meeting_store import KST, VISIBLE, MeetingStore, Viewer
from . import opinion as op

SQL_DIR = Path(__file__).with_name("sql")

_READABLE = ("r.classification <= %(clearance)s AND NOT EXISTS (SELECT 1 FROM meetings m WHERE m.code = ANY(r.meeting_codes) "
             f"AND NOT ({VISIBLE}))")


class AdvisoryStore:
    def __init__(self, meeting_store: MeetingStore):
        self.meeting_store = meeting_store
        self._ready = False
        self._lock = threading.Lock()

    @property
    def schema(self) -> str:
        return self.meeting_store.schema

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
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{self.schema}:adv",))
                conn.execute("""CREATE TABLE IF NOT EXISTS adv_schema_version (version integer PRIMARY KEY, name text NOT NULL,
                                applied_at timestamptz NOT NULL DEFAULT now())""")
                done = {r["version"] for r in conn.execute("SELECT version FROM adv_schema_version")}
                for path in sorted(SQL_DIR.glob("[0-9][0-9][0-9]_*.sql")):
                    version = int(path.name[:3])
                    if version not in done:
                        conn.execute(path.read_text(encoding="utf-8"))
                        conn.execute("INSERT INTO adv_schema_version (version, name) VALUES (%s, %s)", (version, path.name))
                        applied.append(version)
            self._ready = True
            return applied

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        self.migrate()
        with self.meeting_store.connect() as conn:
            yield conn

    # ── write ────────────────────────────────────────────────────────────────

    def save(self, opinion: dict[str, Any], evidence: dict[str, Any], *, viewer: Viewer, company_id: str | None = None) -> dict[str, Any]:
        meetings = op.meeting_codes(evidence)
        with self.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{self.schema}:adv-code",))
            row = conn.execute("SELECT max(classification) AS c FROM meetings WHERE code = ANY(%s)", (meetings,)).fetchone()
            classification = max(1, row["c"] or 1)
            day = datetime.now(KST).date().isoformat()
            n = conn.execute("SELECT count(*) AS n FROM adv_reviews WHERE code LIKE %s", (f"ADV-{day}-%",)).fetchone()["n"]
            code = f"ADV-{day}-{n + 1:02d}"
            conn.execute(
                """INSERT INTO adv_reviews (code, company, company_id, evaluation_type, recommendation, price_trillion_krw, opinion,
                                            evidence, prior_code, classification, meeting_codes, evidence_as_of, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (code, opinion["company"], company_id, opinion["evaluation_type"], opinion["recommendation"],
                 opinion.get("price_trillion_krw"), Jsonb(opinion), Jsonb(evidence), opinion.get("prior_id") or None,
                 classification, meetings, op.evidence_as_of(evidence), viewer.email or viewer.name or None),
            )
        return self.get_unchecked(code)

    def add_details(self, code: str, details: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
        """The second part of a judgement (views, risks, report paragraphs) and the records it cites, merged into the kept one."""
        with self.connect() as conn:
            row = conn.execute("SELECT opinion, evidence FROM adv_reviews WHERE code = %s FOR UPDATE", (code,)).fetchone()
            merged_opinion = {**row["opinion"], **details}
            merged_evidence = {**row["evidence"], "items": {**row["evidence"]["items"], **evidence["items"]}}
            meetings = op.meeting_codes(merged_evidence)
            level = conn.execute("SELECT max(classification) AS c FROM meetings WHERE code = ANY(%s)", (meetings,)).fetchone()["c"] or 1
            conn.execute("""UPDATE adv_reviews SET opinion = %s, evidence = %s, meeting_codes = %s, classification = %s, evidence_as_of = %s
                            WHERE code = %s""",
                         (Jsonb(merged_opinion), Jsonb(merged_evidence), meetings, max(1, level), op.evidence_as_of(merged_evidence), code))
        return self.get_unchecked(code)

    # ── read ─────────────────────────────────────────────────────────────────

    def _rows(self, conn: psycopg.Connection, viewer: Viewer, where: str, params: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        return [_review(r) for r in conn.execute(
            f"SELECT r.* FROM adv_reviews r WHERE {where} AND {_READABLE} ORDER BY r.created_at DESC, r.id DESC LIMIT {int(limit)}",
            {**params, **viewer.scope()})]

    def get(self, viewer: Viewer, code: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            rows = self._rows(conn, viewer, "r.code = %(code)s", {"code": code.strip()}, 1)
        return rows[0] if rows else None

    def exists(self, code: str) -> bool:
        with self.connect() as conn:
            return conn.execute("SELECT 1 FROM adv_reviews WHERE code = %s", (code.strip(),)).fetchone() is not None

    def get_unchecked(self, code: str) -> dict[str, Any]:
        with self.connect() as conn:
            return _review(conn.execute("SELECT r.* FROM adv_reviews r WHERE r.code = %s", (code,)).fetchone())

    def history(self, viewer: Viewer, company: str, limit: int = 5) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return self._rows(conn, viewer, "(r.company ILIKE %(c)s OR r.company_id = %(raw)s OR %(raw)s ILIKE '%%' || r.company || '%%')",
                              {"c": f"%{company.strip()}%", "raw": company.strip()}, limit)

    def latest(self, viewer: Viewer, company: str) -> dict[str, Any] | None:
        rows = self.history(viewer, company, 1)
        return rows[0] if rows else None


def _review(r: dict[str, Any]) -> dict[str, Any]:
    return {"code": r["code"], "company": r["company"], "company_id": r["company_id"], "evaluation_type": r["evaluation_type"],
            "recommendation": r["recommendation"], "price_trillion_krw": float(r["price_trillion_krw"]) if r["price_trillion_krw"] is not None else None,
            "opinion": r["opinion"], "evidence": r["evidence"], "prior_code": r["prior_code"], "classification": r["classification"],
            "meeting_codes": list(r["meeting_codes"]), "evidence_as_of": r["evidence_as_of"].isoformat() if r["evidence_as_of"] else None,
            "created_by": r["created_by"], "created_at": r["created_at"].astimezone(KST).isoformat(timespec="minutes")}
