"""Meeting store — the Postgres database behind the meeting Tools, one schema per agent system.

A system binds its store with MeetingStore(schema, seed); that system's Tools read and write
only that schema. The connection string comes from MEETINGS_DATABASE_URL. In deployment each
system connects with its own DB role, which has no access to the other system's schema.
On first use the schema (meeting_schema.sql, version 1) is created when missing, the numbered
migrations in meeting_migrations/ (NNN_name.sql) it has not had yet are applied in order and
recorded in its schema_migrations table, and, while it has no meetings (no staff directory), its
meetings (staff directory) are seeded from the system's fixture file (data/meetings.json).

Who is asking (`Viewer`) is decided by the Tool layer from the run — never from model
arguments — and every query filters by it in SQL, before anything is read
(gateway shared/access.py): classification <= clearance AND (allowed_groups IS NULL OR they
overlap the viewer's groups).

Not agent-visible; no LangChain dependency.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

SCHEMA_FILE = Path(__file__).with_name("meeting_schema.sql")
MIGRATIONS_DIR = Path(__file__).with_name("meeting_migrations")
KST = timezone(timedelta(hours=9), "KST")  # no DST in Korea; dates and "overdue" are Korean dates
DEFAULT_MEETING_TIME = time(10, 0)

OPEN_STATUSES = ("open", "in_progress", "blocked")
ITEM_STATUSES = ("open", "in_progress", "blocked", "done", "cancelled")


class StoreUnavailable(RuntimeError):
    """No meeting database is configured, or it cannot be reached."""


@dataclass(frozen=True)
class Viewer:
    """The user a Tool call acts for, reduced to what the meeting store checks."""

    email: str | None
    name: str
    groups: frozenset[str]
    clearance: int  # 0 none · 1 공개 · 2 사내 · 3 대외비 · 4 극비
    can_write: bool

    def scope(self) -> dict[str, Any]:
        return {"clearance": self.clearance, "groups": sorted(self.groups)}


def anonymous_clearance() -> int:
    """Clearance of a run without a principal (local demo UI, tests). Deployments leave it at 0."""
    try:
        return max(0, min(4, int(os.environ.get("MEETINGS_ANONYMOUS_CLEARANCE", "0"))))
    except ValueError:
        return 0


def viewer_for(principal: Any | None, origin: str | None) -> Viewer:
    """`principal`: the run's Principal (email, name, groups, clearance) or None.
    A run started by another agent system's A2A request may read this system's records
    (with the forwarded user's clearance) but never change them."""
    can_write = origin != "a2a"
    if principal is None:
        return Viewer(None, "", frozenset(), anonymous_clearance(), can_write)
    return Viewer(principal.email, principal.name or "", frozenset(principal.groups), principal.clearance, can_write)


def confirm_groups() -> frozenset[str] | None:
    """Groups whose members may finalize or correct minutes, review flagged statements, resend, cancel
    or re-address outgoing mail and accept inbound task requests, on any meeting they can see
    (MEETINGS_CONFIRM_GROUPS, comma-separated; "*" = every user who can write, for local demos)."""
    raw = os.environ.get("MEETINGS_CONFIRM_GROUPS", "secretary,executive").strip()
    if raw == "*":
        return None
    return frozenset(g.strip() for g in raw.split(",") if g.strip())


def can_confirm(conn: psycopg.Connection, viewer: Viewer, meeting_id: int | None) -> bool:
    """A confirm group member, or the meeting's own chair / secretary (matched by email)."""
    if not viewer.can_write:
        return False
    groups = confirm_groups()
    if groups is None or viewer.groups & groups:
        return True
    if meeting_id is None or not viewer.email:
        return False
    return conn.execute(
        """SELECT 1 FROM meeting_participants mp JOIN people p ON p.id = mp.person_id
           WHERE mp.meeting_id = %s AND mp.role IN ('chair', 'secretary') AND lower(p.email) = lower(%s)""",
        (meeting_id, viewer.email),
    ).fetchone() is not None


def confirm_denied() -> str:
    groups = confirm_groups() or frozenset()
    return ("회의록 확정 · 정정, Compliance 검토, 메일 재발송 · 취소 · 주소 정정, 업무 요청 접수는 그 회의의 의장 · 간사"
            + (f" 또는 {' · '.join(sorted(groups))} 그룹" if groups else "") + "만 할 수 있습니다.")


# Same rule as gateway shared/access.retrieval_filter, for the meetings row aliased `m`.
VISIBLE = "m.classification <= %(clearance)s AND (m.allowed_groups IS NULL OR m.allowed_groups && %(groups)s::text[])"


class MeetingStore:
    """One agent system's meeting schema."""

    def __init__(self, schema: str, seed: Path | None = None, *, dsn: str | None = None):
        self.schema = schema
        self.seed = seed
        self._dsn = dsn
        self._ready = False
        self._lock = threading.Lock()

    def _connect_raw(self) -> psycopg.Connection:
        dsn = self._dsn or os.environ.get("MEETINGS_DATABASE_URL", "")
        if not dsn:
            raise StoreUnavailable("회의 저장소(MEETINGS_DATABASE_URL)가 설정되지 않았습니다.")
        try:
            conn = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5)
        except psycopg.OperationalError as e:
            raise StoreUnavailable(f"회의 저장소에 연결할 수 없습니다: {e}") from e
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema)))
        return conn

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        """One transaction on this system's schema: committed when the block ends, rolled back on error."""
        self._ensure()
        with self._connect_raw() as conn:
            yield conn

    def reset(self) -> None:
        """Forget that the schema was checked (tests recreate it)."""
        self._ready = False

    def _ensure(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            with self._connect_raw() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (self.schema,))
                # Existence first: a deployment role may lack the privilege to create these, and
                # does not need it once the init script has.
                if not conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'").fetchone():
                    conn.execute("CREATE EXTENSION pg_trgm WITH SCHEMA public")
                if not conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (self.schema,)).fetchone():
                    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
                if conn.execute("SELECT to_regclass(%s) AS t", (f"{self.schema}.meetings",)).fetchone()["t"] is None:
                    conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
                migrate(conn)
                if self.seed is not None:
                    fixture = json.loads(self.seed.read_text(encoding="utf-8"))
                    if conn.execute("SELECT count(*) AS n FROM meetings").fetchone()["n"] == 0:
                        seed_fixture(conn, fixture)
                    if not conn.execute("SELECT 1 FROM staff_directory LIMIT 1").fetchone():
                        seed_directory(conn, fixture.get("directory", []))
            self._ready = True


def migrations() -> list[tuple[int, str, Path]]:
    """(version, name, file) of meeting_migrations/NNN_name.sql, in order. Version 1 is meeting_schema.sql."""
    found = []
    for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        found.append((int(path.name[:3]), path.stem[4:], path))
    return found


def migrate(conn: psycopg.Connection) -> list[int]:
    """Applies the migrations this schema has not had, in order, in the caller's transaction."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version int PRIMARY KEY, name text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"""
    )
    conn.execute("INSERT INTO schema_migrations (version, name) VALUES (1, 'meeting_schema') ON CONFLICT DO NOTHING")
    applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    done = []
    for version, name, path in migrations():
        if version in applied:
            continue
        conn.execute(path.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO schema_migrations (version, name) VALUES (%s, %s)", (version, name))
        done.append(version)
    return done


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def today_kst() -> date:
    return datetime.now(KST).date()


def day_label(day: date) -> str:
    """2026-10-27 (화): the model has no clock, so results that depend on today say which day it is."""
    return f"{day.isoformat()} ({'월화수목금토일'[day.weekday()]})"


def parse_date(value: str | None) -> date | None:
    """YYYY-MM-DD → date; None/'' → None. Raises ValueError on anything else."""
    if not value:
        return None
    return date.fromisoformat(str(value).strip()[:10])


def parse_datetime(value: str) -> datetime:
    """ISO date-time; a value without an offset is Korean time. A bare date gets 10:00."""
    text = str(value).strip().replace(" ", "T", 1)
    dt = datetime.fromisoformat(text) if "T" in text else datetime.combine(date.fromisoformat(text), DEFAULT_MEETING_TIME)
    return dt if dt.tzinfo else dt.replace(tzinfo=KST)


def kst_date(ts: datetime | None) -> str | None:
    return ts.astimezone(KST).date().isoformat() if ts else None


def kst_minute(ts: datetime | None) -> str | None:
    return ts.astimezone(KST).strftime("%Y-%m-%d %H:%M") if ts else None


def time_label(starts: datetime | None, ends: datetime | None) -> str | None:
    """14:00~15:00 (KST), or just 14:00 without an end."""
    if not starts:
        return None
    text = starts.astimezone(KST).strftime("%H:%M")
    return f"{text}~{ends.astimezone(KST).strftime('%H:%M')}" if ends else text


def parse_time_of_day(value: str | None) -> time | None:
    """'14:05' (KST) → time; None/'' → None. Raises ValueError on anything else."""
    if not value or not str(value).strip():
        return None
    hour, minute = str(value).strip().split(":")[:2]
    return time(int(hour), int(minute))


# ---------------------------------------------------------------------------
# People · org units (the directory the records point to)
# ---------------------------------------------------------------------------

_LABELLED = re.compile(r"^\s*(.+?)\s*\(([^()]+)\)\s*$")  # "박지훈 (CFO)"
# An owner label naming a unit rather than a person: 재무기획팀, 제조본부, 그룹 전략실, CTO 직속 …
_UNIT = re.compile(r"(팀|본부|실|부|센터|위원회|직속|법인|사업부)$")

PERSON_LABEL = "p.display_name || coalesce(' (' || p.title || ')', '')"
OWNER_LABEL = f"coalesce({PERSON_LABEL}, u.name)"


def split_label(label: str) -> tuple[str, str | None]:
    m = _LABELLED.match(label)
    return (m.group(1).strip(), m.group(2).strip()) if m else (label.strip(), None)


# Words in a label's parentheses that say what the person does at this meeting, not their job title.
_ROLE_WORDS = {"의장": "chair", "간사": "secretary", "배석": "observer", "선택참석": "optional"}
# A courtesy suffix after the name ("정전략 실장", "한상우 위원") is not part of the name.
_COURTESY = re.compile(r"\s+(님|실장|위원|위원장|팀장|본부장|부장|차장|과장|대리|이사|상무|전무|부사장|사장|대표|교수|변호사|회계사)$")


def _squash(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def parse_person_label(label: str) -> tuple[str, str | None, str | None]:
    """(name, title, meeting role) of a label: "김회장 (의장)" → chair with no title,
    "박지훈 (K전자 CFO, 간사)" → title "K전자 CFO" and secretary, "정전략 실장" → name 정전략."""
    name, inside = split_label(label)
    role, parts = None, []
    for part in (inside or "").split(","):
        word = part.strip()
        if _squash(word) in _ROLE_WORDS:
            role = role or _ROLE_WORDS[_squash(word)]
        elif word:
            parts.append(word)
    return _COURTESY.sub("", name).strip() or name, ", ".join(parts) or None, role


def find_person(conn: psycopg.Connection, label: str) -> int | None:
    """The person a label names: same name, and when the label has a title, the same title — or one that
    contains or is contained in it ("K전자 CFO" ~ "CFO")."""
    name, title, _ = parse_person_label(label)
    rows = conn.execute("SELECT id, title FROM people WHERE lower(display_name) = lower(%s) ORDER BY id", (name,)).fetchall()
    if title:
        rows = [r for r in rows if r["title"] == title] or [
            r for r in rows if r["title"] and (_squash(r["title"]) in _squash(title) or _squash(title) in _squash(r["title"]))
        ]
    return rows[0]["id"] if rows else None


def person_id(conn: psycopg.Connection, label: str, created: list[str], *, title: str | None = None) -> int:
    """Existing person by name (and title, if the label has one); otherwise a new directory entry."""
    name, label_title, _ = parse_person_label(label)
    title = title or label_title
    found = find_person(conn, f"{name} ({title})" if title else name)
    if found is not None:
        return found
    created.append(f"사람: {name}" + (f" ({title})" if title else ""))
    return conn.execute(
        "INSERT INTO people (display_name, title) VALUES (%s, %s) RETURNING id", (name, title)
    ).fetchone()["id"]


def resolve_owner(conn: psycopg.Connection, label: str | None, created: list[str]) -> tuple[int | None, int | None]:
    """(person_id, org_unit_id) for an Action Item owner label; a label nobody has is registered."""
    if not label or not label.strip():
        return None, None
    label = label.strip()
    unit = conn.execute("SELECT id FROM org_units WHERE lower(name) = lower(%s)", (label,)).fetchone()
    if unit:
        return None, unit["id"]
    person = find_person(conn, label)
    if person is not None:
        return person, None
    if _UNIT.search(label) and len(label) >= 3:
        created.append(f"조직: {label}")
        return None, conn.execute("INSERT INTO org_units (name) VALUES (%s) RETURNING id", (label,)).fetchone()["id"]
    return person_id(conn, label, created), None


# ---------------------------------------------------------------------------
# Codes
# ---------------------------------------------------------------------------


def new_meeting_code(conn: psycopg.Connection, day: date) -> str:
    """MTG-2026-10-25-01, -02, … (fixture meetings keep their own ids)."""
    prefix = f"MTG-{day.isoformat()}-"
    taken = {r["code"] for r in conn.execute("SELECT code FROM meetings WHERE code LIKE %s", (prefix + "%",))}
    n = 1
    while f"{prefix}{n:02d}" in taken:
        n += 1
    return f"{prefix}{n:02d}"


def next_item_number(conn: psycopg.Connection, meeting_id: int, code: str) -> int:
    rows = conn.execute("SELECT code FROM action_items WHERE meeting_id = %s", (meeting_id,)).fetchall()
    numbers = [int(m.group(1)) for r in rows if (m := re.fullmatch(re.escape(code) + r"-A(\d+)", r["code"]))]
    return max(numbers, default=0) + 1


def decision_ref(meeting_code: str, seq: int) -> str:
    """How a decision is named to the model and the user: MTG-2026-09-15-D1."""
    return f"{meeting_code}-D{seq}"


def find_decision(conn: psycopg.Connection, ref: str, viewer: Viewer) -> int | None:
    m = re.fullmatch(r"\s*(.+)-D(\d+)\s*", ref or "")
    if not m:
        return None
    row = conn.execute(
        f"SELECT d.id FROM decisions d JOIN meetings m ON m.id = d.meeting_id WHERE m.code = %(code)s AND d.seq = %(seq)s AND {VISIBLE}",
        {"code": m.group(1), "seq": int(m.group(2)), **viewer.scope()},
    ).fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------------------
# Seed: a system's fixture file → its empty schema
# ---------------------------------------------------------------------------


def seed_fixture(conn: psycopg.Connection, data: dict[str, Any]) -> None:
    """Fixture meetings become held meetings with final minutes. Optional fields:
    classification (default 2), summary, decisions[].kind, action_items[].due."""
    created: list[str] = []
    for m in data["meetings"]:
        starts = datetime.combine(date.fromisoformat(m["date"]), DEFAULT_MEETING_TIME, KST)
        meeting = conn.execute(
            """INSERT INTO meetings (code, title, status, starts_at, topics, classification, created_by)
               VALUES (%s, %s, 'held', %s, %s, %s, 'fixture') RETURNING id""",
            (m["id"], m["title"], starts, m.get("topics", []), m.get("classification", 2)),
        ).fetchone()["id"]
        summary = m.get("summary") or f"{m['title']}: 결정사항 {len(m['decisions'])}건, Action Item {len(m['action_items'])}건"
        conn.execute(
            """INSERT INTO minutes (meeting_id, status, summary, drafted_by, finalized_by, finalized_at)
               VALUES (%s, 'final', %s, 'fixture', 'fixture', %s)""",
            (meeting, summary, starts + timedelta(days=1)),
        )
        for seq, d in enumerate(m["decisions"], 1):
            conn.execute(
                "INSERT INTO decisions (meeting_id, seq, kind, speaker_label, statement) VALUES (%s, %s, %s, %s, %s)",
                (meeting, seq, d.get("kind", "decision"), d.get("speaker"), d["statement"]),
            )
        for n, a in enumerate(m["action_items"], 1):
            owner_person, owner_unit = resolve_owner(conn, a.get("owner"), created)
            status = a.get("status", "open")
            conn.execute(
                """INSERT INTO action_items (code, meeting_id, text, owner_person_id, owner_unit_id, due_date, status, completed_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (f"{m['id']}-A{n}", meeting, a["text"], owner_person, owner_unit, parse_date(a.get("due")), status,
                 starts + timedelta(days=7) if status == "done" else None),
            )


def seed_directory(conn: psycopg.Connection, entries: list[dict[str, Any]]) -> None:
    """The fixture's staff · contacts directory (name, email; optional title, department, organization, external)."""
    for e in entries:
        conn.execute(
            """INSERT INTO staff_directory (name, title, department, organization, email, is_external)
               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (email) DO NOTHING""",
            (e["name"], e.get("title"), e.get("department"), e.get("organization"), e["email"], bool(e.get("external"))),
        )
