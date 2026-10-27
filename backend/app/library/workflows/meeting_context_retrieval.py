"""Meeting context retrieval — implementation workflow behind the
run_meeting_context_retrieval Tool (app/library/tools/).

Reads the meetings the viewer may see from the system's meeting store (only meetings
whose minutes are final, unless one meeting is asked for by id), scores each against
the request and returns the decisions, open issues and open Action Items of the matches.
The Meeting Agent never touches the store directly; the tool is the boundary.
This module is not agent-visible and has no LangChain dependency.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .meeting_store import OPEN_STATUSES, OWNER_LABEL, PERSON_LABEL, VISIBLE, MeetingStore, Viewer, decision_ref, kst_date, parse_date, today_kst


_MIN_TOKEN_LEN = 2
# A meeting needs at least one topic hit (3) or title + statement hits to count.
_MIN_SCORE = 3
# Filler words that appear in almost any request and carry no topic signal.
_STOPWORDS = {
    "관련", "관련해", "관련된", "대한", "대해", "지난", "최근", "회의", "전략회의",
    "회의록", "기록", "결정", "결정한", "결정사항", "기준", "확인", "확인되지", "않은",
    "아직", "찾아줘", "찾아", "조회", "정리", "미완료", "및", "그리고",
    "item", "items", "action", "open", "meeting", "meetings",
}


def _terms(query: str, topics: list[str]) -> set[str]:
    """Search terms from the query and topic hints.

    Korean particles are stripped crudely by also adding each token's prefixes
    (뉴로엣지의 → 뉴로엣지, 인수와 → 인수). Single characters are ignored.
    """
    out: set[str] = set()
    for raw in re.findall(r"[0-9A-Za-z가-힣&.\-]+", f"{query} {' '.join(topics)}".lower()):
        if len(raw) < _MIN_TOKEN_LEN or raw in _STOPWORDS:
            continue
        out.add(raw)
        for n in range(_MIN_TOKEN_LEN, len(raw)):
            prefix = raw[:n]
            if prefix not in _STOPWORDS:
                out.add(prefix)
    return out


def _hit(text: str, terms: set[str]) -> bool:
    text = text.lower()
    return any(t in text for t in terms)


def score_meeting(meeting: dict[str, Any], query: str, topics: list[str]) -> int:
    """Keyword overlap score. Topic hits weigh most, then title, then statements."""
    terms = _terms(query, topics)
    score = 0
    for topic in meeting["topics"]:
        t = topic.lower()
        if t in terms or _hit(t, terms):
            score += 3
    if _hit(meeting["title"], terms):
        score += 2
    for d in meeting["decisions"]:
        if _hit(d["statement"], terms):
            score += 1
    return score


def _load(store: MeetingStore, viewer: Viewer, *, meeting_id: str | None, date_from: date | None,
          date_to: date | None, today: date) -> list[dict[str, Any]]:
    """Visible meetings in the store's own shape, with decisions, open issues and open items."""
    params = {**viewer.scope(), "code": meeting_id, "date_from": date_from, "date_to": date_to, "today": today}
    with store.connect() as conn:
        meetings = conn.execute(
            f"""SELECT m.id, m.code, m.starts_at, m.title, m.topics, m.classification, mi.status AS minutes_status, mi.summary
                FROM meetings m JOIN minutes mi ON mi.meeting_id = m.id
                WHERE {VISIBLE}
                  AND (mi.status = 'final' OR m.code = %(code)s)
                  AND (%(code)s::text IS NULL OR m.code = %(code)s)
                  AND (%(date_from)s::date IS NULL OR (m.starts_at AT TIME ZONE 'Asia/Seoul')::date >= %(date_from)s)
                  AND (%(date_to)s::date IS NULL OR (m.starts_at AT TIME ZONE 'Asia/Seoul')::date <= %(date_to)s)""",
            params,
        ).fetchall()
        ids = [m["id"] for m in meetings]
        decisions = conn.execute(
            f"""SELECT d.meeting_id, d.seq, d.kind, d.statement, d.rationale, d.outcome, d.resolved_by_id,
                       coalesce({PERSON_LABEL}, d.speaker_label) AS speaker
                FROM decisions d LEFT JOIN people p ON p.id = d.speaker_id
                WHERE d.meeting_id = ANY(%(ids)s::bigint[]) ORDER BY d.meeting_id, d.seq""",
            {"ids": ids},
        ).fetchall()
        items = conn.execute(
            f"""SELECT a.meeting_id, a.code, a.text, {OWNER_LABEL} AS owner, a.due_date, a.status,
                       CASE WHEN a.due_date < %(today)s THEN %(today)s - a.due_date ELSE 0 END AS overdue_days
                FROM action_items a LEFT JOIN people p ON p.id = a.owner_person_id LEFT JOIN org_units u ON u.id = a.owner_unit_id
                WHERE a.meeting_id = ANY(%(ids)s::bigint[]) AND a.status = ANY(%(open)s) ORDER BY a.meeting_id, a.id""",
            {"ids": ids, "today": today, "open": list(OPEN_STATUSES)},
        ).fetchall()
    out = []
    for m in meetings:
        own = [d for d in decisions if d["meeting_id"] == m["id"]]
        out.append({
            "id": m["code"],
            "date": kst_date(m["starts_at"]),
            "title": m["title"],
            "status": m["minutes_status"],
            "classification": m["classification"],
            "topics": list(m["topics"]),
            "summary": m["summary"],
            "decisions": [
                {"ref": decision_ref(m["code"], d["seq"]), "kind": d["kind"], "speaker": d["speaker"], "statement": d["statement"]}
                | ({"rationale": d["rationale"]} if d["rationale"] else {})
                | ({"outcome": d["outcome"]} if d["outcome"] else {})
                for d in own if d["kind"] != "issue"
            ],
            "open_issues": [
                {"ref": decision_ref(m["code"], d["seq"]), "statement": d["statement"]}
                for d in own if d["kind"] == "issue" and d["resolved_by_id"] is None
            ],
            "open_action_items": [
                {"id": a["code"], "text": a["text"], "owner": a["owner"], "due": a["due_date"].isoformat() if a["due_date"] else None,
                 "status": a["status"], "overdue_days": a["overdue_days"]}
                for a in items if a["meeting_id"] == m["id"]
            ],
        })
    return out


def retrieve_meeting_context(
    query: str,
    topics: list[str] | None = None,
    *,
    store: MeetingStore,
    viewer: Viewer,
    meeting_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 3,
    today: date | None = None,
) -> dict[str, Any]:
    """The store is the system's own (bound by its Tool); `viewer` is who asks.
    Meetings the viewer may not see are never loaded, so they cannot match."""
    topics = topics or []
    try:
        start, end = parse_date(date_from), parse_date(date_to)
    except ValueError:
        return {"query": query, "topics": topics, "matched": 0, "meetings": [], "error": "날짜는 YYYY-MM-DD 형식이어야 합니다."}
    meetings = _load(store, viewer, meeting_id=meeting_id, date_from=start, date_to=end, today=today or today_kst())
    if meeting_id:
        matches = meetings[:1]  # asked by id: no scoring, drafts included
    else:
        ranked = sorted(
            ((score_meeting(m, query, topics), m) for m in meetings),
            key=lambda pair: (-pair[0], pair[1]["date"]),
        )
        # Absolute floor plus a relative one: a meeting that scores far below the
        # best match only shares an incidental keyword (e.g. "투자") with the query.
        best = ranked[0][0] if ranked else 0
        threshold = max(_MIN_SCORE, best // 2)
        matches = [m for s, m in ranked if s >= threshold][:limit]
    return {"query": query, "topics": topics, "matched": len(matches), "meetings": matches}
