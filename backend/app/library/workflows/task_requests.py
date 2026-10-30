"""Task requests from another agent system — implementation workflow behind the run_task_requests Tool.

A run started by another system's A2A request may read this system's records but not change
them, with one exception: it may *submit* a task request (submit), which waits as pending.
Only someone here who may confirm records (meeting_store.can_confirm) turns it into an official
Action Item (accept: owner and due date required, attached to the meeting given or to an
intake record of its own) or turns it down (reject). The requesting system can ask how its
requests stand (list over A2A shows only its own). Not agent-visible; no LangChain dependency.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import psycopg

from .meeting_store import (
    DEFAULT_MEETING_TIME,
    KST,
    OWNER_LABEL,
    VISIBLE,
    MeetingStore,
    Viewer,
    can_confirm,
    confirm_denied,
    day_label,
    new_meeting_code,
    next_item_number,
    parse_date,
    resolve_owner,
    today_kst,
)
from .outbox import queue_assignment_mail, summarize, unit_owned

ACTIONS = ("submit", "list", "accept", "reject")
SYSTEM_NAMES = {"group": "그룹", "k_electronics": "K전자"}


def _fail(action: str, error: str) -> dict[str, Any]:
    return {"ok": False, "action": action, "error": error}


def _new_code(conn: psycopg.Connection, day: date) -> str:
    prefix = f"REQ-{day.isoformat()}-"
    taken = {r["code"] for r in conn.execute("SELECT code FROM task_requests WHERE code LIKE %s", (prefix + "%",))}
    n = 1
    while f"{prefix}{n:02d}" in taken:
        n += 1
    return f"{prefix}{n:02d}"


def _request(conn: psycopg.Connection, request_id: int) -> dict[str, Any]:
    r = conn.execute(
        f"""SELECT t.*, a.code AS item_code, a.status AS item_status, a.due_date AS item_due, {OWNER_LABEL} AS item_owner,
                   (SELECT u2.note FROM action_item_updates u2 WHERE u2.action_item_id = a.id AND u2.note IS NOT NULL
                     ORDER BY u2.at DESC LIMIT 1) AS item_note
            FROM task_requests t LEFT JOIN action_items a ON a.id = t.action_item_id
            LEFT JOIN people p ON p.id = a.owner_person_id LEFT JOIN org_units u ON u.id = a.owner_unit_id
            WHERE t.id = %s""",
        (request_id,),
    ).fetchone()
    out = {
        "id": r["code"],
        "from": SYSTEM_NAMES.get(r["from_system"], r["from_system"]),
        "requested_by": r["requester_name"] or r["requested_by"],
        "source_ref": r["source_ref"],
        "title": r["title"],
        "detail": r["detail"],
        "suggested_owner": r["suggested_owner"],
        "due": r["due_date"].isoformat() if r["due_date"] else None,
        "classification": r["classification"],
        "status": r["status"],
        "received_at": r["created_at"].astimezone(KST).strftime("%Y-%m-%d %H:%M"),
    }
    if r["status"] != "pending":
        out["decision_note"] = r["decision_note"]
    if r["item_code"]:
        out["action_item"] = {"id": r["item_code"], "owner": r["item_owner"], "status": r["item_status"],
                              "due": r["item_due"].isoformat() if r["item_due"] else None, "last_note": r["item_note"]}
    return out


def task_requests(
    *,
    store: MeetingStore,
    viewer: Viewer,
    caller: str | None,
    action: str = "list",
    request_id: str | None = None,
    title: str | None = None,
    detail: str | None = None,
    due: str | None = None,
    suggested_owner: str | None = None,
    source_ref: str | None = None,
    classification: int = 2,
    owner: str | None = None,
    meeting_id: str | None = None,
    note: str | None = None,
    status: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """`caller`: the requesting agent system when the run came over A2A (RunContext.caller), else None."""
    if action not in ACTIONS:
        return _fail(action, f"action은 {', '.join(ACTIONS)} 중 하나여야 합니다.")
    today = today or today_kst()
    from_a2a = not viewer.can_write

    with store.connect() as conn:
        if action == "submit":
            if not from_a2a or not caller:
                return _fail(action, "업무 요청 접수(submit)는 다른 Agent System의 A2A 요청으로만 들어옵니다. "
                                     "이 시스템의 업무는 회의록 Action Item으로 등록하세요.")
            if not (title and title.strip()):
                return _fail(action, "요청할 업무(title)가 필요합니다.")
            if classification not in (1, 2, 3, 4) or classification > viewer.clearance:
                return _fail(action, f"요청 등급은 1~4 중 요청자의 열람 등급({viewer.clearance}) 이하여야 합니다.")
            try:
                due_date = parse_date(due)
            except ValueError:
                return _fail(action, "due는 YYYY-MM-DD 형식이어야 합니다.")
            code = _new_code(conn, today)
            rid = conn.execute(
                """INSERT INTO task_requests (code, from_system, requested_by, requester_name, source_ref, title, detail,
                                              suggested_owner, due_date, classification)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (code, caller, viewer.email, viewer.name or None, (source_ref or "").strip() or None, title.strip(),
                 (detail or "").strip() or None, (suggested_owner or "").strip() or None, due_date, classification),
            ).fetchone()["id"]
            return {"ok": True, "action": action, "request": _request(conn, rid),
                    "next": "수신 측 확정 권한자가 확인한 뒤 정식 Action Item으로 등록됩니다."}

        if action == "list":
            where = ["t.classification <= %(clearance)s"]
            params: dict[str, Any] = {"clearance": viewer.clearance}
            if from_a2a:  # a requesting system sees only its own requests
                where.append("t.from_system = %(caller)s")
                params["caller"] = caller or ""
            if request_id:
                where.append("t.code = %(code)s")
                params["code"] = request_id.strip()
            elif status in ("pending", "accepted", "rejected"):
                where.append("t.status = %(status)s")
                params["status"] = status
            elif not from_a2a:
                where.append("t.status = 'pending'")
            rows = conn.execute(
                f"SELECT t.id FROM task_requests t WHERE {' AND '.join(where)} ORDER BY t.created_at DESC LIMIT 30", params
            ).fetchall()
            return {"ok": True, "action": action, "today": day_label(today), "matched": len(rows),
                    "requests": [_request(conn, r["id"]) for r in rows]}

        # accept / reject: this system's people decide
        if from_a2a:
            return _fail(action, "다른 Agent System이 보낸 요청으로는 업무 요청을 접수 · 반려할 수 없습니다.")
        if not request_id:
            return _fail(action, "request_id(예: REQ-2026-10-28-01)가 필요합니다.")
        row = conn.execute(
            "SELECT * FROM task_requests WHERE code = %s AND classification <= %s FOR UPDATE",
            (request_id.strip(), viewer.clearance),
        ).fetchone()
        if row is None:
            return _fail(action, f"업무 요청 '{request_id}'를 찾을 수 없거나 열람 권한이 없습니다.")
        if row["status"] != "pending":
            return _fail(action, f"이미 '{row['status']}' 처리된 요청입니다.")

        target = None
        if meeting_id:
            target = conn.execute(
                f"SELECT m.id, m.code FROM meetings m WHERE m.code = %(code)s AND {VISIBLE}",
                {"code": meeting_id.strip(), **viewer.scope()},
            ).fetchone()
            if target is None:
                return _fail(action, f"회의 '{meeting_id}'를 찾을 수 없거나 열람 권한이 없습니다.")
        if not can_confirm(conn, viewer, target["id"] if target else None):
            return _fail(action, confirm_denied())

        if action == "reject":
            conn.execute(
                "UPDATE task_requests SET status = 'rejected', decided_by = %s, decided_at = now(), decision_note = %s WHERE id = %s",
                (viewer.email, (note or "").strip() or None, row["id"]),
            )
            return {"ok": True, "action": action, "request": _request(conn, row["id"])}

        try:
            due_date = parse_date(due) or row["due_date"]
        except ValueError:
            return _fail(action, "due는 YYYY-MM-DD 형식이어야 합니다.")
        owner_label = (owner or "").strip() or row["suggested_owner"]
        if not owner_label or due_date is None:
            return _fail(action, "정식 Action Item으로 등록하려면 담당(owner)과 기한(due)이 필요합니다.")
        created: list[str] = []
        if target is None:
            # An intake record of its own: the request's Action Item is tracked like any other (final record).
            source = SYSTEM_NAMES.get(row["from_system"], row["from_system"])
            code = new_meeting_code(conn, today)
            mid = conn.execute(
                """INSERT INTO meetings (code, title, status, starts_at, topics, classification, created_by)
                   VALUES (%s, %s, 'held', %s, %s, %s, %s) RETURNING id""",
                (code, f"{source} 업무 요청 접수: {row['title']}", datetime.combine(today, DEFAULT_MEETING_TIME, KST),
                 ["업무 요청", source], row["classification"], viewer.email),
            ).fetchone()["id"]
            conn.execute(
                """INSERT INTO minutes (meeting_id, status, summary, body, drafted_by, finalized_by, finalized_at)
                   VALUES (%s, 'final', %s, %s, %s, %s, now())""",
                (mid, f"{source}의 업무 요청 {row['code']}을 접수했다: {row['title']}", row["detail"], viewer.email, viewer.email),
            )
            target = {"id": mid, "code": code}
        owner_person, owner_unit = resolve_owner(conn, owner_label, created)
        item_code = f"{target['code']}-A{next_item_number(conn, target['id'], target['code'])}"
        text = row["title"] + (f" ({row['detail']})" if row["detail"] else "")
        item_id = conn.execute(
            """INSERT INTO action_items (code, meeting_id, text, owner_person_id, owner_unit_id, due_date)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (item_code, target["id"], text, owner_person, owner_unit, due_date),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO action_item_updates (action_item_id, by_email, new_status, note) VALUES (%s, %s, 'open', %s)",
            (item_id, viewer.email, f"{row['code']} 접수 ({row['from_system']} 요청" + (f", 원 기록 {row['source_ref']}" if row["source_ref"] else "") + ")"),
        )
        conn.execute(
            """UPDATE task_requests SET status = 'accepted', decided_by = %s, decided_at = now(), decision_note = %s, action_item_id = %s
               WHERE id = %s""",
            (viewer.email, (note or "").strip() or None, item_id, row["id"]),
        )
        mail_id = queue_assignment_mail(conn, item_id, viewer)
        return {"ok": True, "action": action, "request": _request(conn, row["id"]), "meeting_id": target["code"],
                "mail_queued": summarize(conn, [mail_id] if mail_id else []), "notes": unit_owned(conn, target["id"], [item_id]),
                "new_directory_entries": created}
