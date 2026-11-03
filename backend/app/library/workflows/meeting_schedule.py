"""Meeting schedule — implementation workflow behind the run_meeting_schedule Tool.

The system's own calendar of meetings (no external calendar sync): list upcoming meetings,
create one with its agenda (optionally carrying over what an earlier meeting deferred or
left unresolved), change it, or cancel it — each queues a notice to the participants for
approval (outbox.py). Minutes are later written onto the same meeting
id with save_meeting_minutes. Not agent-visible; no LangChain dependency.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import psycopg

from .meeting_store import (
    KST,
    PERSON_LABEL,
    VISIBLE,
    MeetingStore,
    Viewer,
    day_label,
    decision_ref,
    kst_minute,
    new_meeting_code,
    parse_date,
    parse_datetime,
    parse_person_label,
    person_id,
    today_kst,
)
from .outbox import queue_schedule_mail

ACTIONS = ("list", "create", "update", "cancel")
UPCOMING_DAYS = 30


def _fail(action: str, error: str) -> dict[str, Any]:
    return {"ok": False, "action": action, "error": error}


def scheduled_meeting(conn: psycopg.Connection, meeting_id: int) -> dict[str, Any]:
    m = conn.execute(
        """SELECT m.code, m.title, m.status, m.starts_at, m.ends_at, m.location, m.classification, s.name AS series
           FROM meetings m LEFT JOIN meeting_series s ON s.id = m.series_id WHERE m.id = %s""",
        (meeting_id,),
    ).fetchone()
    agenda = conn.execute(
        """SELECT a.seq, a.title, a.status, pm.code AS carried_from FROM agenda_items a
           LEFT JOIN agenda_items prev ON prev.id = a.carried_from LEFT JOIN meetings pm ON pm.id = prev.meeting_id
           WHERE a.meeting_id = %s ORDER BY a.seq""",
        (meeting_id,),
    ).fetchall()
    people = conn.execute(
        f"""SELECT {PERSON_LABEL} AS name, mp.role, mp.rsvp FROM meeting_participants mp JOIN people p ON p.id = mp.person_id
            WHERE mp.meeting_id = %s ORDER BY p.display_name""",
        (meeting_id,),
    ).fetchall()
    out = {
        "id": m["code"],
        "title": m["title"],
        "status": m["status"],
        "starts_at": kst_minute(m["starts_at"]),
        "ends_at": kst_minute(m["ends_at"]),
        "location": m["location"],
        "classification": m["classification"],
        "agenda": [{"seq": a["seq"], "title": a["title"], "status": a["status"]} | ({"carried_from": a["carried_from"]} if a["carried_from"] else {})
                   for a in agenda],
        "participants": [{"name": p["name"], "role": p["role"], "rsvp": p["rsvp"]} for p in people],
    }
    if m["series"]:
        out["series"] = m["series"]
    return out


def _add_participants(conn: psycopg.Connection, meeting_id: int, names: list[str], created: list[str]) -> None:
    """Participants by label; "(의장)" · "(간사)" in a label sets that meeting role."""
    for name in names:
        if name and name.strip():
            role = parse_person_label(name)[2] or "required"
            conn.execute(
                """INSERT INTO meeting_participants (meeting_id, person_id, role) VALUES (%s, %s, %s)
                   ON CONFLICT (meeting_id, person_id) DO UPDATE SET role = EXCLUDED.role WHERE EXCLUDED.role <> 'required'""",
                (meeting_id, person_id(conn, name, created, meeting_id=meeting_id), role),
            )


def _add_agenda(conn: psycopg.Connection, meeting_id: int, titles: list[str], carried: list[tuple[str, int | None, str | None]]) -> None:
    """New agenda after what is already there: carried-over items first, then the given titles."""
    seq = conn.execute("SELECT coalesce(max(seq), 0) AS n FROM agenda_items WHERE meeting_id = %s", (meeting_id,)).fetchone()["n"]
    for title, carried_from, description in carried:
        seq += 1
        conn.execute(
            "INSERT INTO agenda_items (meeting_id, seq, title, description, carried_from) VALUES (%s, %s, %s, %s, %s)",
            (meeting_id, seq, title, description, carried_from),
        )
    for title in titles:
        if title and title.strip():
            seq += 1
            conn.execute("INSERT INTO agenda_items (meeting_id, seq, title) VALUES (%s, %s, %s)", (meeting_id, seq, title.strip()))


def _carry_over(conn: psycopg.Connection, viewer: Viewer, code: str) -> list[tuple[str, int | None, str | None]] | None:
    """Deferred agenda items and unresolved issues of an earlier meeting, as new agenda entries. Once a meeting
    was held its minutes say what stayed open (issues), so its still-'planned' items count as discussed."""
    source = conn.execute(
        f"SELECT m.id, m.status FROM meetings m WHERE m.code = %(code)s AND {VISIBLE}", {"code": code.strip(), **viewer.scope()}
    ).fetchone()
    if source is None:
        return None
    statuses = ["deferred"] if source["status"] == "held" else ["deferred", "planned"]
    deferred = conn.execute(
        "SELECT id, title FROM agenda_items WHERE meeting_id = %s AND status = ANY(%s) ORDER BY seq", (source["id"], statuses)
    ).fetchall()
    issues = conn.execute(
        "SELECT seq, statement FROM decisions WHERE meeting_id = %s AND kind = 'issue' AND resolved_by_id IS NULL ORDER BY seq",
        (source["id"],),
    ).fetchall()
    return [(a["title"], a["id"], None) for a in deferred] + [
        (i["statement"], None, f"이월 쟁점 {decision_ref(code.strip(), i['seq'])}") for i in issues
    ]


def meeting_schedule(
    *,
    store: MeetingStore,
    viewer: Viewer,
    action: str = "list",
    meeting_id: str | None = None,
    title: str | None = None,
    starts_at: str | None = None,
    ends_at: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
    agenda: list[str] | None = None,
    classification: int = 2,
    carry_over_from: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    mine: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    if action not in ACTIONS:
        return _fail(action, f"action은 {', '.join(ACTIONS)} 중 하나여야 합니다.")
    today = today or today_kst()
    created: list[str] = []

    if action == "list":
        try:
            start = parse_date(date_from) or today
            end = parse_date(date_to) or start + timedelta(days=UPCOMING_DAYS)
        except ValueError:
            return _fail(action, "날짜는 YYYY-MM-DD 형식이어야 합니다.")
        if mine and not (viewer.email or viewer.name):
            return _fail(action, "요청자 정보가 없어 '내 회의'를 찾을 수 없습니다.")
        with store.connect() as conn:
            rows = conn.execute(
                f"""SELECT m.id FROM meetings m
                    WHERE {VISIBLE} AND m.status IN ('scheduled', 'postponed')
                      AND (m.starts_at AT TIME ZONE 'Asia/Seoul')::date BETWEEN %(start)s AND %(end)s
                      AND (NOT %(mine)s OR EXISTS (
                            SELECT 1 FROM meeting_participants mp JOIN people p ON p.id = mp.person_id
                            WHERE mp.meeting_id = m.id AND (p.email = %(email)s OR lower(p.display_name) = lower(%(name)s))))
                    ORDER BY m.starts_at LIMIT 50""",
                {**viewer.scope(), "start": start, "end": end, "mine": mine, "email": viewer.email, "name": viewer.name or None},
            ).fetchall()
            meetings = [scheduled_meeting(conn, r["id"]) for r in rows]
        return {"ok": True, "action": action, "today": day_label(today), "from": start.isoformat(), "to": end.isoformat(),
                "matched": len(meetings), "meetings": meetings}

    if not viewer.can_write:
        return _fail(action, "다른 Agent System이 보낸 요청으로는 회의 일정을 바꿀 수 없습니다.")
    try:
        start_ts = parse_datetime(starts_at) if starts_at else None
        end_ts = parse_datetime(ends_at) if ends_at else None
    except ValueError:
        return _fail(action, "starts_at / ends_at은 'YYYY-MM-DD HH:MM' 형식이어야 합니다 (한국 시간).")
    if start_ts and end_ts and end_ts <= start_ts:
        return _fail(action, "종료 시각이 시작 시각보다 뒤여야 합니다.")

    with store.connect() as conn:
        if action == "create":
            if not (title and title.strip()) or start_ts is None:
                return _fail(action, "새 회의에는 title과 starts_at이 필요합니다.")
            if classification not in (1, 2, 3, 4) or classification > viewer.clearance:
                return _fail(action, f"회의 등급은 1~4 중 요청자의 열람 등급({viewer.clearance}) 이하여야 합니다.")
            if start_ts.astimezone(KST).date() < today:
                return _fail(action, "지난 날짜에는 회의를 예약할 수 없습니다. 이미 열린 회의라면 save_meeting_minutes로 기록하세요.")
            carried: list[tuple[str, int | None, str | None]] = []
            if carry_over_from:
                found = _carry_over(conn, viewer, carry_over_from)
                if found is None:
                    return _fail(action, f"이월할 회의 '{carry_over_from}'를 찾을 수 없거나 열람 권한이 없습니다.")
                carried = found
            code = new_meeting_code(conn, start_ts.astimezone(KST).date())
            mid = conn.execute(
                """INSERT INTO meetings (code, title, status, starts_at, ends_at, location, classification, created_by)
                   VALUES (%s, %s, 'scheduled', %s, %s, %s, %s, %s) RETURNING id""",
                (code, title.strip(), start_ts, end_ts, location, classification, viewer.email),
            ).fetchone()["id"]
            _add_participants(conn, mid, attendees or [], created)
            _add_agenda(conn, mid, agenda or [], carried)
            mails = queue_schedule_mail(conn, mid, action, viewer)
            return {"ok": True, "action": action, "meeting": scheduled_meeting(conn, mid), "mail_queued": mails, "new_directory_entries": created}

        if not meeting_id:
            return _fail(action, "바꿀 회의의 meeting_id가 필요합니다.")
        row = conn.execute(
            f"SELECT m.id, m.status, m.starts_at, m.ends_at FROM meetings m WHERE m.code = %(code)s AND {VISIBLE} FOR UPDATE",
            {"code": meeting_id.strip(), **viewer.scope()},
        ).fetchone()
        if row is None:
            return _fail(action, f"회의 '{meeting_id}'를 찾을 수 없거나 열람 권한이 없습니다.")
        if row["status"] not in ("scheduled", "postponed"):
            return _fail(action, f"이미 '{row['status']}' 상태인 회의는 일정 변경 · 취소 대상이 아닙니다.")
        if start_ts and end_ts is None and row["ends_at"] is not None:
            end_ts = start_ts + (row["ends_at"] - row["starts_at"])  # 시작만 옮기면 회의 길이는 그대로
        if end_ts and start_ts is None and end_ts <= row["starts_at"]:
            return _fail(action, "종료 시각이 시작 시각보다 뒤여야 합니다.")
        if action == "cancel":
            conn.execute("UPDATE meetings SET status = 'cancelled', updated_at = now() WHERE id = %s", (row["id"],))
        else:
            conn.execute(
                """UPDATE meetings SET title = coalesce(%s, title), starts_at = coalesce(%s, starts_at),
                                       ends_at = coalesce(%s, ends_at), location = coalesce(%s, location), updated_at = now()
                   WHERE id = %s""",
                ((title or "").strip() or None, start_ts, end_ts, location, row["id"]),
            )
            _add_participants(conn, row["id"], attendees or [], created)
            _add_agenda(conn, row["id"], agenda or [], [])
        mails = queue_schedule_mail(conn, row["id"], action, viewer)
        return {"ok": True, "action": action, "meeting": scheduled_meeting(conn, row["id"]), "mail_queued": mails,
                "new_directory_entries": created}
