"""Meeting subjects (주제) — the business matter a meeting carries on (천안 신공장, 뉴로엣지 인수).

A subject groups the meetings of one matter across bodies and weeks, so the desk can show the whole company on one
wall (meeting_control). A meeting gets its subject, in this order:
  the one the caller names (save_meeting_minutes' subject)  ·  the one it already has
  the meeting it carries over from (a booked re-review)     ·  its title against the subjects in use —
  a title word that is or holds a word of the subject ('투자심의위원회 (천안신공장 투자건)' → 천안 신공장)
and a title that matches none starts a subject of its own: the title without meeting words ('신공장 추진 착수 회의' → 신공장).
Meetings saved before subjects existed get theirs the same way the first time the desk reads them (fill_missing).
"""

from __future__ import annotations

import re

import psycopg

from .meeting_store import VISIBLE, Viewer

_WORD = re.compile(r"[0-9A-Za-z가-힣]+")
_SUFFIX = re.compile(r"(회의록|회의|위원회|건)$")
# Words any meeting title has: they say what kind of meeting it is, not which matter
MEETING_WORDS = {
    "추진", "착수", "킥오프", "검토", "점검", "진행", "상황", "선정", "결과", "보고", "준비", "후속", "정기", "재검토", "논의", "계획",
    "투자", "투자심의", "심의", "인수", "출시", "협상", "실사", "전략", "그룹", "임원", "경영", "주간", "월간", "1차", "2차", "3차", "사전",
}


def subject_words(text: str | None) -> list[str]:
    out = []
    for w in _WORD.findall(text or ""):
        w = _SUFFIX.sub("", w)
        if len(w) >= 2 and w not in MEETING_WORDS:
            out.append(w)
    return out


def match_subject(conn: psycopg.Connection, viewer: Viewer, title: str) -> str | None:
    """The subject in use that the title names (most words in common; the latest one when tied)."""
    words = subject_words(title)
    if not words:
        return None
    best: tuple[int, str] | None = None
    for r in conn.execute(
        f"""SELECT m.subject, max(m.starts_at) AS last FROM meetings m WHERE {VISIBLE} AND m.subject IS NOT NULL
            GROUP BY m.subject ORDER BY last DESC""",
        viewer.scope(),
    ):
        hits = sum(1 for s in subject_words(r["subject"]) if any(s in w or w in s for w in words))
        if hits and (best is None or hits > best[0]):
            best = (hits, r["subject"])
    return best[1] if best else None


def fill_missing(conn: psycopg.Connection, viewer: Viewer) -> int:
    """Meetings from before subjects (migration 007) that the viewer can see: a subject from the title, oldest first,
    so a later meeting joins the subject an earlier one started."""
    rows = conn.execute(
        f"SELECT m.id, m.title FROM meetings m WHERE m.subject IS NULL AND {VISIBLE} ORDER BY m.starts_at, m.id", viewer.scope(),
    ).fetchall()
    for r in rows:
        conn.execute("UPDATE meetings SET subject = %s WHERE id = %s", (subject_for(conn, viewer, r["title"]), r["id"]))
    return len(rows)


def subject_for(conn: psycopg.Connection, viewer: Viewer, title: str, *, named: str | None = None,
                carried_from: int | None = None) -> str | None:
    if named and named.strip():
        return " ".join(named.split())
    if carried_from:
        row = conn.execute("SELECT subject FROM meetings WHERE id = %s", (carried_from,)).fetchone()
        if row and row["subject"]:
            return row["subject"]
    found = match_subject(conn, viewer, title)
    if found:
        return found
    words = subject_words(title)
    return " ".join(words[:2]) or " ".join((title or "").split()) or None  # only meeting words: the title itself
