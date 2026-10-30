"""회의 업무 화면 — 사람이 직접 보고 누르는 경로 (Agent · LLM을 거치지 않음).

배포 화면(Open WebUI)은 대화뿐이라, 회의 기록을 목록으로 보고 메일 · Compliance 검토 · Action Item을
버튼으로 처리하는 화면(gateway의 /meetings/)이 system app의 /api/desk/* 를 거쳐 이 함수들을 부른다.
같은 저장소 · 같은 권한 규칙(VISIBLE · can_confirm · can_write)을 쓰고, 조회 · 검토는 미팅 Tool이 쓰는
workflow 함수를 그대로 부른다. 새로 둔 것은 여러 회의를 한 번에 보는 조회(overview · open_flags)뿐이다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .action_items import track_action_items, update_action_item
from .compliance import apply_reviews, flag_ref, flags_of
from .meeting_context_retrieval import retrieve_meeting_context
from .meeting_schedule import meeting_schedule, scheduled_meeting
from .meeting_store import PERSON_LABEL, VISIBLE, MeetingStore, Viewer, can_confirm, confirm_denied, kst_date, time_label, today_kst
from .outbox import manage_outbox

UPCOMING_DAYS = 14
MINUTES_LIMIT = 60
ROLE_ORDER = ("chair", "secretary", "required", "optional", "observer")
VIEWS = ("mine", "all", "overdue")


def overview(*, store: MeetingStore, viewer: Viewer, today: date | None = None) -> dict[str, Any]:
    """첫 화면: 다가오는 회의, 최근 회의록(초안 먼저), 내 Action Item, 메일 · 검토 건수."""
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
                "flags": flags_of(conn, mid), "can_confirm": can_confirm(conn, viewer, mid)}
