"""Action Item tracking and updates — implementation workflows behind the
run_action_item_tracking and update_action_item Tools.

Tracking reads Action Items across meetings (only meetings with final minutes, unless one
meeting is asked for), overdue first. An update never overwrites history: every change is
a row in action_item_updates with who reported it. Not agent-visible; no LangChain dependency.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from .meeting_store import (
    ITEM_STATUSES,
    OPEN_STATUSES,
    OWNER_LABEL,
    VISIBLE,
    MeetingStore,
    Viewer,
    day_label,
    kst_date,
    parse_date,
    resolve_owner,
    today_kst,
)

_TOKEN = re.compile(r"[0-9A-Za-z가-힣]{2,}")


def _item_rows(conn, where: str, params: dict[str, Any], *, limit: int | None = None) -> list[dict[str, Any]]:
    return conn.execute(
        f"""SELECT a.id, a.code, a.text, {OWNER_LABEL} AS owner, a.due_date, a.status,
                   CASE WHEN a.status = ANY(%(open)s) AND a.due_date < %(today)s THEN %(today)s - a.due_date ELSE 0 END AS overdue_days,
                   m.code AS meeting_code, m.title AS meeting_title, m.starts_at,
                   (SELECT u2.note FROM action_item_updates u2 WHERE u2.action_item_id = a.id AND u2.note IS NOT NULL
                     ORDER BY u2.at DESC LIMIT 1) AS last_note
            FROM action_items a JOIN meetings m ON m.id = a.meeting_id JOIN minutes mi ON mi.meeting_id = m.id
            LEFT JOIN people p ON p.id = a.owner_person_id LEFT JOIN org_units u ON u.id = a.owner_unit_id
            WHERE {VISIBLE} AND {where}
            ORDER BY overdue_days DESC, a.due_date NULLS LAST, a.code
            {"LIMIT %(limit)s" if limit else ""}""",
        {**params, "open": list(OPEN_STATUSES), "limit": limit},
    ).fetchall()


def _item(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["code"],
        "text": r["text"],
        "owner": r["owner"],
        "due": r["due_date"].isoformat() if r["due_date"] else None,
        "status": r["status"],
        "overdue_days": r["overdue_days"],
        "last_note": r["last_note"],
        "meeting": {"id": r["meeting_code"], "date": kst_date(r["starts_at"]), "title": r["meeting_title"]},
    }


def track_action_items(
    *,
    store: MeetingStore,
    viewer: Viewer,
    query: str | None = None,
    owner: str | None = None,
    status: list[str] | None = None,
    due_before: str | None = None,
    due_within_days: int | None = None,
    overdue_only: bool = False,
    meeting_id: str | None = None,
    mine: bool = False,
    limit: int = 30,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or today_kst()
    try:
        before = parse_date(due_before)
    except ValueError:
        return {"error": "due_before는 YYYY-MM-DD 형식이어야 합니다."}
    if due_within_days is not None:
        within = today + timedelta(days=max(0, due_within_days))
        before = min(before, within) if before else within
    statuses = [s for s in (status or OPEN_STATUSES) if s in ITEM_STATUSES] or list(OPEN_STATUSES)
    patterns = [f"%{t}%" for t in _TOKEN.findall(query or "")]
    if mine and not (viewer.email or viewer.name):
        return {"error": "요청자 정보가 없어 '내 담당' 항목을 찾을 수 없습니다."}
    # The scope (who, what, which meeting) — status and due filters are applied on top of it,
    # so the summary counts show the whole picture of that scope.
    scope = """(mi.status = 'final' OR m.code = %(meeting)s)
               AND (%(meeting)s::text IS NULL OR m.code = %(meeting)s)
               AND (cardinality(%(patterns)s::text[]) = 0 OR (a.text || ' ' || m.title) ILIKE ANY(%(patterns)s::text[]))
               AND (%(owner)s::text IS NULL OR p.display_name ILIKE %(owner_like)s OR p.title ILIKE %(owner_like)s OR u.name ILIKE %(owner_like)s)
               AND (NOT %(mine)s OR p.email = %(email)s OR lower(p.display_name) = lower(%(name)s)
                    OR a.owner_unit_id IN (SELECT org_unit_id FROM people WHERE email = %(email)s))"""
    params = {
        **viewer.scope(), "today": today, "meeting": meeting_id, "patterns": patterns, "owner": owner,
        "owner_like": f"%{owner}%" if owner else None, "mine": mine, "email": viewer.email, "name": viewer.name or None,
        "statuses": statuses, "before": before, "overdue_only": overdue_only,
    }
    filters = """a.status = ANY(%(statuses)s)
                 AND (%(before)s::date IS NULL OR a.due_date <= %(before)s)
                 AND (NOT %(overdue_only)s OR (a.status = ANY(%(open)s) AND a.due_date < %(today)s))"""
    with store.connect() as conn:
        in_scope = _item_rows(conn, scope, params)
        rows = _item_rows(conn, f"{scope} AND {filters}", params, limit=max(1, min(limit, 100)))
    summary = {s: sum(1 for r in in_scope if r["status"] == s) for s in ITEM_STATUSES}
    summary["overdue"] = sum(1 for r in in_scope if r["overdue_days"] > 0)
    return {
        "as_of": day_label(today),
        "filters": {k: v for k, v in {"query": query, "owner": owner, "status": statuses,
                                      "due_before": before.isoformat() if before else None,
                                      "overdue_only": overdue_only or None, "meeting_id": meeting_id, "mine": mine or None}.items() if v},
        "summary": summary,
        "matched": len(rows),
        "items": [_item(r) for r in rows],
    }


def update_action_item(
    *,
    store: MeetingStore,
    viewer: Viewer,
    item_id: str,
    status: str | None = None,
    note: str | None = None,
    due: str | None = None,
    owner: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or today_kst()
    if not viewer.can_write:
        return {"updated": False, "error": "다른 Agent System이 보낸 요청으로는 Action Item을 바꿀 수 없습니다."}
    if status is not None and status not in ITEM_STATUSES:
        return {"updated": False, "error": f"status는 {', '.join(ITEM_STATUSES)} 중 하나여야 합니다."}
    try:
        new_due = parse_date(due)
    except ValueError:
        return {"updated": False, "error": "due는 YYYY-MM-DD 형식이어야 합니다."}
    if status is None and not note and new_due is None and not owner:
        return {"updated": False, "error": "바꿀 내용(status, note, due, owner)이 하나 이상 있어야 합니다."}
    code = (item_id or "").strip()
    with store.connect() as conn:
        row = conn.execute(
            f"""SELECT a.id, a.status, a.due_date, {OWNER_LABEL} AS owner FROM action_items a JOIN meetings m ON m.id = a.meeting_id
                LEFT JOIN people p ON p.id = a.owner_person_id LEFT JOIN org_units u ON u.id = a.owner_unit_id
                WHERE upper(a.code) = upper(%(code)s) AND {VISIBLE} FOR UPDATE OF a""",
            {"code": code, **viewer.scope()},
        ).fetchone()
        if row is None:
            candidates = conn.execute(
                f"""SELECT a.code, a.text, a.status FROM action_items a JOIN meetings m ON m.id = a.meeting_id
                    WHERE {VISIBLE} AND (a.code ILIKE %(like)s OR similarity(a.text, %(code)s) > 0.15)
                    ORDER BY similarity(a.text, %(code)s) DESC, a.code LIMIT 5""",
                {"code": code, "like": f"%{code}%", **viewer.scope()},
            ).fetchall()
            return {
                "updated": False,
                "error": f"Action Item '{code}'을 찾을 수 없거나 열람 권한이 없습니다.",
                "candidates": [{"id": c["code"], "text": c["text"], "status": c["status"]} for c in candidates],
            }
        changes = []
        created: list[str] = []
        sets, values = ["updated_at = now()"], []
        if status is not None and status != row["status"]:
            sets.append("status = %s")
            values.append(status)
            sets.append("completed_at = CASE WHEN %s = 'done' THEN now() END")
            values.append(status)
        if new_due is not None and new_due != row["due_date"]:
            sets.append("due_date = %s")
            values.append(new_due)
            changes.append(f"기한 {row['due_date'] or '없음'} → {new_due}")
        if owner:
            owner_person, owner_unit = resolve_owner(conn, owner, created)
            sets.append("owner_person_id = %s, owner_unit_id = %s")
            values += [owner_person, owner_unit]
            changes.append(f"담당 {row['owner'] or '없음'} → {owner.strip()}")
        conn.execute(f"UPDATE action_items SET {', '.join(sets)} WHERE id = %s", (*values, row["id"]))
        text = "; ".join([*changes, note.strip()] if note else changes) or None
        conn.execute(
            "INSERT INTO action_item_updates (action_item_id, by_email, old_status, new_status, note) VALUES (%s, %s, %s, %s, %s)",
            (row["id"], viewer.email, row["status"], status or row["status"], text),
        )
        item = _item_rows(conn, "a.id = %(id)s", {**viewer.scope(), "id": row["id"], "today": today})[0]
        history = conn.execute(
            "SELECT at, by_email, old_status, new_status, note FROM action_item_updates WHERE action_item_id = %s ORDER BY at DESC, id DESC LIMIT 5",
            (row["id"],),
        ).fetchall()
    return {
        "updated": True,
        "item": _item(item),
        "history": [
            {"at": h["at"].isoformat(timespec="minutes"), "by": h["by_email"], "status": f"{h['old_status']} → {h['new_status']}", "note": h["note"]}
            for h in history
        ],
        "new_directory_entries": created,
    }
