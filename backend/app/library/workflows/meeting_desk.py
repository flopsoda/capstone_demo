"""회의 업무 화면 — 사람이 직접 보고 누르는 경로 (Agent · LLM을 거치지 않음).

배포 화면(Open WebUI)은 대화뿐이라, 회의 기록을 목록으로 보고 메일 · Compliance 검토 · Action Item을
버튼으로 처리하는 화면(gateway의 /meetings/)이 system app의 /api/desk/* 를 거쳐 이 함수들을 부른다.
같은 저장소 · 같은 권한 규칙(VISIBLE · can_confirm · can_write)을 쓰고, 조회 · 검토는 미팅 Tool이 쓰는
workflow 함수를 그대로 부른다. 새로 둔 것은 여러 회의를 한 번에 보는 조회(overview · open_flags)뿐이다.
출석 확인(QR) · 참석자 확인 · '각자' 할 일 나누기는 attendance.py.
전제가 흔들린 결정(뉴스 · 보고 신호)과 그 확인은 decision_watch.py.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from . import attendance as attendance_flow
from .action_items import track_action_items, update_action_item
from .compliance import apply_reviews, flag_ref, flags_of
from .decision_watch import open_signals
from .meeting_context_retrieval import retrieve_meeting_context
from .meeting_schedule import meeting_schedule, scheduled_meeting
from .meeting_minutes import save_minutes
from .meeting_store import (
    PERSON_LABEL, VISIBLE, MeetingStore, Viewer, can_confirm, confirm_denied, kst_date, kst_minute, time_label, today_kst,
)
from .outbox import manage_outbox

UPCOMING_DAYS = 14
MINUTES_LIMIT = 60
MINUTES_PAGE = 15
MINUTES_STATUSES = ("draft", "final")
ROLE_ORDER = ("chair", "secretary", "required", "optional", "observer")
VIEWS = ("mine", "all", "overdue")


def overview(*, store: MeetingStore, viewer: Viewer, today: date | None = None) -> dict[str, Any]:
    """첫 화면: 다가오는 회의, 최근 회의록(초안 먼저), 내 Action Item, 메일 · 검토 건수, 전제가 흔들린 결정."""
    today = today or today_kst()
    upcoming = meeting_schedule(store=store, viewer=viewer, action="list", date_from=today.isoformat(),
                                date_to=(today + timedelta(days=UPCOMING_DAYS)).isoformat(), today=today)
    mine = track_action_items(store=store, viewer=viewer, mine=True, limit=10, today=today) if viewer.email else {}
    with store.connect() as conn:
        minutes = conn.execute(
            f"""SELECT m.code, m.title, m.starts_at, m.ends_at, m.classification, mi.status, mi.version,
                       (SELECT count(*) FROM compliance_flags f WHERE f.meeting_id = m.id AND f.status = 'open') AS open_flags,
                       (SELECT count(*) FROM decisions d WHERE d.meeting_id = m.id) AS decisions,
                       (SELECT count(*) FROM action_items a WHERE a.meeting_id = m.id) AS items
                FROM meetings m JOIN minutes mi ON mi.meeting_id = m.id
                WHERE {VISIBLE} ORDER BY (mi.status = 'draft') DESC, m.starts_at DESC LIMIT %(limit)s""",
            {**viewer.scope(), "limit": MINUTES_LIMIT},
        ).fetchall()
        mail = conn.execute(
            f"""SELECT count(*) FILTER (WHERE o.status IN ('pending', 'approved')) AS pending,
                       count(*) FILTER (WHERE o.status = 'needs_address') AS needs_address,
                       count(*) FILTER (WHERE o.status = 'failed') AS failed,
                       count(*) FILTER (WHERE o.sent_at > now() - interval '7 days') AS sent
                FROM outbox o JOIN meetings m ON m.id = o.meeting_id WHERE {VISIBLE}""",
            viewer.scope(),
        ).fetchone()
        flags = conn.execute(
            f"""SELECT count(*) AS n FROM compliance_flags f JOIN meetings m ON m.id = f.meeting_id
                WHERE {VISIBLE} AND f.status = 'open'""",
            viewer.scope(),
        ).fetchone()["n"]
        watch = open_signals(conn, viewer)
    items = mine.get("items", [])
    return {
        "today": today.isoformat(),
        "viewer": {"name": viewer.name, "email": viewer.email},
        "counts": {"mail_pending": mail["pending"], "mail_needs_address": mail["needs_address"], "mail_failed": mail["failed"],
                   "mail_sent": mail["sent"], "flags_open": flags,
                   "my_items": len(items), "my_overdue": sum(1 for i in items if i["overdue_days"] > 0)},
        "upcoming": upcoming.get("meetings", []),
        "minutes": [{"id": r["code"], "title": r["title"], "date": kst_date(r["starts_at"]),
                     "time": time_label(r["starts_at"], r["ends_at"]), "status": r["status"],
                     "version": r["version"], "classification": r["classification"], "open_flags": r["open_flags"],
                     "decisions": r["decisions"], "items": r["items"]} for r in minutes],
        "my_items": items,
        "watch": watch,
    }


def _like(word: str) -> str:
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def minutes_list(*, store: MeetingStore, viewer: Viewer, query: str | None = None, status: str | None = None,
                 offset: int | None = 0) -> dict[str, Any]:
    """회의록 찾기: 회의명 · 회의 id · 주제 · 요약에 검색어(띄어 쓴 낱말 모두)가 든 회의록을 최근 순으로 한 쪽씩.
    초안 · 확정으로 거를 수 있고, 같은 검색어의 초안 · 확정 건수를 함께 돌려준다."""
    if status and status not in MINUTES_STATUSES:
        return {"error": "status는 draft(초안) 또는 final(확정)입니다."}
    offset = max(0, int(offset or 0))
    clauses, params = [VISIBLE], {**viewer.scope()}
    for i, word in enumerate((query or "").split()[:5]):
        clauses.append(f"""(m.title ILIKE %(w{i})s OR m.code ILIKE %(w{i})s OR mi.summary ILIKE %(w{i})s
                          OR array_to_string(m.topics, ' ') ILIKE %(w{i})s)""")
        params[f"w{i}"] = _like(word)
    where = " AND ".join(clauses)
    only = "AND mi.status = %(status)s" if status else ""
    with store.connect() as conn:
        counts = conn.execute(
            f"""SELECT count(*) AS total, count(*) FILTER (WHERE mi.status = 'draft') AS draft,
                       count(*) FILTER (WHERE mi.status = 'final') AS final
                FROM meetings m JOIN minutes mi ON mi.meeting_id = m.id WHERE {where}""",
            params,
        ).fetchone()
        rows = conn.execute(
            f"""SELECT m.code, m.title, m.starts_at, m.ends_at, m.classification, mi.status, mi.version,
                       (SELECT count(*) FROM compliance_flags f WHERE f.meeting_id = m.id AND f.status = 'open') AS open_flags
                FROM meetings m JOIN minutes mi ON mi.meeting_id = m.id
                WHERE {where} {only}
                ORDER BY m.starts_at DESC, m.id DESC LIMIT %(limit)s OFFSET %(offset)s""",
            {**params, "status": status, "limit": MINUTES_PAGE + 1, "offset": offset},
        ).fetchall()
    return {
        "counts": dict(counts),
        "offset": offset,
        "more": len(rows) > MINUTES_PAGE,
        "minutes": [{"id": r["code"], "title": r["title"], "date": kst_date(r["starts_at"]), "time": time_label(r["starts_at"], r["ends_at"]),
                     "status": r["status"], "version": r["version"], "classification": r["classification"],
                     "open_flags": r["open_flags"]} for r in rows[:MINUTES_PAGE]],
    }


def mail(*, store: MeetingStore, viewer: Viewer, action: str = "list", mail_ids: list[str] | None = None,
         email: str | None = None, note: str | None = None) -> dict[str, Any]:
    """메일 발송함: 목록 · 다시 보내기 · 취소 · 주소 정정 (manage_mail_outbox Tool과 같은 workflow)."""
    return manage_outbox(store=store, viewer=viewer, action=action, mail_ids=mail_ids, email=email, note=note)


def open_flags(*, store: MeetingStore, viewer: Viewer) -> dict[str, Any]:
    """볼 수 있는 회의의 검토 안 된 Compliance 표시."""
    with store.connect() as conn:
        rows = conn.execute(
            f"""SELECT f.id, f.location, f.rule, f.excerpt, m.code, m.title, m.starts_at
                FROM compliance_flags f JOIN meetings m ON m.id = f.meeting_id
                WHERE {VISIBLE} AND f.status = 'open' ORDER BY m.starts_at DESC, f.id""",
            viewer.scope(),
        ).fetchall()
    return {"flags": [{"id": flag_ref(r["id"]), "location": r["location"], "rule": r["rule"], "excerpt": r["excerpt"],
                       "meeting": {"id": r["code"], "title": r["title"], "date": kst_date(r["starts_at"])}} for r in rows]}


def review_flags(*, store: MeetingStore, viewer: Viewer, meeting_id: str | None,
                 reviews: list[dict[str, Any]] | None) -> dict[str, Any]:
    """[{id: CF-n, status: cleared | acknowledged, note}] → 그 회의의 표시. 권한은 회의록 확정과 같다."""
    if not viewer.can_write:
        return {"ok": False, "error": "다른 Agent System이 보낸 요청으로는 검토할 수 없습니다."}
    if not meeting_id or not reviews:
        return {"ok": False, "error": "meeting_id와 reviews가 필요합니다."}
    with store.connect() as conn:
        m = conn.execute(f"SELECT m.id FROM meetings m WHERE m.code = %(code)s AND {VISIBLE}",
                         {**viewer.scope(), "code": meeting_id.strip()}).fetchone()
        if not m:
            return {"ok": False, "error": "해당하는 회의가 없거나 열람 권한이 없습니다."}
        if not can_confirm(conn, viewer, m["id"]):
            return {"ok": False, "error": confirm_denied()}
        warnings: list[str] = []
        apply_reviews(conn, m["id"], reviews, viewer, warnings)
        return {"ok": not warnings, "warnings": warnings, "flags": flags_of(conn, m["id"])}


def items(*, store: MeetingStore, viewer: Viewer, view: str = "mine", today: date | None = None) -> dict[str, Any]:
    """Action Item: 내 담당 · 전체 · 기한 지남."""
    if view not in VIEWS:
        return {"error": f"view는 {', '.join(VIEWS)} 중 하나여야 합니다."}
    if view == "mine" and not viewer.email:
        return {"error": "요청자 정보가 없어 '내 담당' 항목을 찾을 수 없습니다."}
    return track_action_items(store=store, viewer=viewer, mine=view == "mine", overdue_only=view == "overdue",
                              limit=100, today=today)


def update_item(*, store: MeetingStore, viewer: Viewer, item_id: str, status: str | None = None,
                note: str | None = None, owner: str | None = None, due: str | None = None,
                today: date | None = None) -> dict[str, Any]:
    """완료 · 진행 중 표시와 담당 · 기한 지정 (확정된 회의록이면 새 담당자에게 업무 배정 메일)."""
    return update_action_item(store=store, viewer=viewer, item_id=item_id, status=status, note=note, owner=owner, due=due,
                              today=today)


SCHEDULE_ACTIONS = ("create", "update", "cancel")


def schedule(*, store: MeetingStore, viewer: Viewer, action: str, meeting_id: str | None = None, title: str | None = None,
             starts_at: str | None = None, ends_at: str | None = None, location: str | None = None,
             attendees: list[str] | None = None, agenda: list[str] | None = None, classification: int | None = None,
             today: date | None = None) -> dict[str, Any]:
    """회의 일정 만들기 · 바꾸기 · 취소 (run_meeting_schedule Tool과 같은 workflow). 안내 메일은 Agent가 바로 보낸다."""
    if action not in SCHEDULE_ACTIONS:
        return {"ok": False, "error": f"action은 {', '.join(SCHEDULE_ACTIONS)} 중 하나여야 합니다."}
    return meeting_schedule(store=store, viewer=viewer, action=action, meeting_id=meeting_id, title=title, starts_at=starts_at,
                            ends_at=ends_at, location=location, attendees=attendees, agenda=agenda,
                            classification=classification or 2, today=today)


def meeting(*, store: MeetingStore, viewer: Viewer, meeting_id: str | None, today: date | None = None) -> dict[str, Any]:
    """회의 하나: 회의록 · 결정 · 열린 Action Item(retrieval과 같은 모양) + 참석자 + Compliance 표시 + 확정 권한.
    회의록이 아직 없는 예약된 회의는 {scheduled: 일정 · 안건 · 참석 대상}."""
    if not meeting_id:
        return {"error": "meeting_id가 필요합니다."}
    found = retrieve_meeting_context("", store=store, viewer=viewer, meeting_id=meeting_id.strip(), today=today)
    if not found["meetings"]:
        with store.connect() as conn:
            row = conn.execute(f"SELECT m.id FROM meetings m WHERE m.code = %(code)s AND {VISIBLE}",
                               {"code": meeting_id.strip(), **viewer.scope()}).fetchone()
            if row is None:
                return {"error": "해당하는 회의가 없거나 열람 권한이 없습니다."}
            return {"scheduled": scheduled_meeting(conn, row["id"])}
    with store.connect() as conn:
        mid = conn.execute("SELECT id FROM meetings WHERE code = %s", (meeting_id.strip(),)).fetchone()["id"]
        people = conn.execute(
            f"""SELECT {PERSON_LABEL} AS name, mp.role FROM meeting_participants mp JOIN people p ON p.id = mp.person_id
                WHERE mp.meeting_id = %s ORDER BY array_position(%s::text[], mp.role), p.display_name""",
            (mid, list(ROLE_ORDER)),
        ).fetchall()
        return {"meeting": found["meetings"][0], "participants": [{"name": r["name"], "role": r["role"]} for r in people],
                "flags": flags_of(conn, mid), "can_confirm": can_confirm(conn, viewer, mid),
                "attendance": attendance_flow.attendance_view(conn, mid), "recall": attendance_flow.recall(conn, mid, viewer, today),
                "can_edit": viewer.can_write, **_meeting_record(conn, mid)}


def _meeting_record(conn, mid: int) -> dict[str, Any]:
    """회의 관제의 회의 칸: 녹음 파일, 이 회의의 할 일 전부(끝난 것 포함 — 진행률), 이 회의가 보낸 메일."""
    recordings = conn.execute(
        """SELECT t.source_name, t.duration_s, count(s.seq) AS lines, count(DISTINCT s.speaker_label) AS speakers
           FROM transcripts t LEFT JOIN transcript_segments s ON s.transcript_id = t.id WHERE t.meeting_id = %s
           GROUP BY t.id ORDER BY t.id""", (mid,)).fetchall()
    items = conn.execute(
        f"""SELECT a.code, a.text, a.status, a.due_date, coalesce({PERSON_LABEL}, u.name) AS owner FROM action_items a
            LEFT JOIN people p ON p.id = a.owner_person_id LEFT JOIN org_units u ON u.id = a.owner_unit_id
            WHERE a.meeting_id = %s ORDER BY a.id""", (mid,)).fetchall()
    mails = conn.execute(
        """SELECT o.id, o.kind, o.to_name, o.status, o.subject, o.sent_at FROM outbox o WHERE o.meeting_id = %s ORDER BY o.id""",
        (mid,)).fetchall()
    return {
        "recordings": [{"name": r["source_name"], "seconds": round(r["duration_s"] or 0), "lines": r["lines"], "speakers": r["speakers"]}
                       for r in recordings],
        "items": [{"id": i["code"], "text": i["text"], "status": i["status"], "owner": i["owner"],
                   "due": i["due_date"].isoformat() if i["due_date"] else None} for i in items],
        "mails": [{"id": f"MAIL-{m['id']}", "kind": m["kind"], "to": m["to_name"], "status": m["status"], "subject": m["subject"],
                   "sent_at": kst_minute(m["sent_at"])} for m in mails],
    }


def finalize(*, store: MeetingStore, viewer: Viewer, meeting_id: str | None) -> dict[str, Any]:
    """회의 관제의 '확정 · 메일 발송': 저장된 초안을 그대로 확정한다 (Agent를 거치지 않음 — 목록을 보내지 않으면 저장된 것을 둔다)."""
    if not meeting_id:
        return {"ok": False, "error": "meeting_id가 필요합니다."}
    with store.connect() as conn:
        row = conn.execute(
            f"""SELECT m.code, m.title, m.starts_at, mi.summary, mi.status FROM meetings m JOIN minutes mi ON mi.meeting_id = m.id
                WHERE m.code = %(code)s AND {VISIBLE}""", {"code": meeting_id.strip(), **viewer.scope()}).fetchone()
    if row is None:
        return {"ok": False, "error": "회의록 초안이 없는 회의입니다."}
    if row["status"] == "final":
        return {"ok": False, "error": "이미 확정된 회의록입니다."}
    return save_minutes(store=store, viewer=viewer, title=row["title"], date=kst_date(row["starts_at"]), summary=row["summary"] or "",
                        meeting_id=row["code"], finalize=True)


CHECKIN_ACTIONS = ("open", "status", "close", "join")


def checkin(*, store: MeetingStore, viewer: Viewer, action: str, code: str | None = None, token: str | None = None,
            voice_consent: bool | None = None, source_name: str | None = None) -> dict[str, Any]:
    """출석 확인: 녹음을 올린 화면이 열고(open) QR 상태를 묻고(status) 닫는다(close). 휴대폰은 출석한다(join)."""
    if action == "open":
        return attendance_flow.open_session(store=store, viewer=viewer, source_name=source_name)
    if action == "status":
        return attendance_flow.session_status(store=store, viewer=viewer, code=code)
    if action == "close":
        return attendance_flow.close_session(store=store, viewer=viewer, code=code)
    if action == "join":
        return attendance_flow.join(store=store, viewer=viewer, code=code, token=token, voice_consent=voice_consent)
    return {"ok": False, "error": f"action은 {', '.join(CHECKIN_ACTIONS)} 중 하나여야 합니다."}


ATTENDANCE_ACTIONS = ("assign", "add", "remove")


def attendance(*, store: MeetingStore, viewer: Viewer, action: str, meeting_id: str | None, speaker: str | None = None,
               person: dict[str, Any] | None = None) -> dict[str, Any]:
    """회의 카드의 참석자 확인: 목소리 라벨의 이름 정하기(assign) · 참석자 더하기(add) · 빼기(remove)."""
    if action == "assign":
        return attendance_flow.assign_speaker(store=store, viewer=viewer, meeting_id=meeting_id, speaker=speaker, person=person)
    if action == "add":
        return attendance_flow.add_attendee(store=store, viewer=viewer, meeting_id=meeting_id, person=person)
    if action == "remove":
        return attendance_flow.remove_attendee(store=store, viewer=viewer, meeting_id=meeting_id, person=person)
    return {"ok": False, "error": f"action은 {', '.join(ATTENDANCE_ACTIONS)} 중 하나여야 합니다."}


def split_item(*, store: MeetingStore, viewer: Viewer, item_id: str | None) -> dict[str, Any]:
    """'각자' 하는 할 일 → 참석자마다 한 건."""
    return attendance_flow.split_item(store=store, viewer=viewer, item_id=item_id)
