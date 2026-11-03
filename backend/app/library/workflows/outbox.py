"""Mail outbox — the email channel of the meeting workflows (to people; other agent systems get A2A).

Workflows queue mail here: minutes to the participants and assignments to the owners when minutes
are finalized or corrected, notices when a scheduled meeting is created, changed or cancelled,
and due-date reminders (queued by the daily job). The agent finds each recipient's address itself
(people.email, else the email registered at login found by name — directory.lookup_email — else
the staff · contacts directory) and queues the mail ready to go (approved by 'agent'); no person
approves it. The delivery job sends it over SMTP (SMTP_HOST …, see deliver_approved). Mail to
someone found nowhere waits as needs_address and is looked up again every morning; an address is
never guessed. People may still resend failed mail, cancel mail not yet sent, or correct an
address (manage_outbox). Not agent-visible; no LangChain dependency.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import smtplib
from datetime import date, datetime, timezone
from email.message import EmailMessage
from email.headerregistry import Address
from email.utils import formatdate, make_msgid
from typing import Any

import psycopg

from .directory import lookup_email
from .meeting_store import (
    OPEN_STATUSES,
    PERSON_LABEL,
    VISIBLE,
    MeetingStore,
    Viewer,
    can_confirm,
    confirm_denied,
    kst_date,
    kst_minute,
    unnamed_speaker,
)

log = logging.getLogger("agents")

ACTIONS = ("list", "resend", "reject", "set_address")
AGENT = "agent"  # approved_by of mail the agent queued ready to send
MAX_ATTEMPTS = 5
STALE_DAYS = 3  # mail not sent within this many days (no mail server yet) is dropped, not sent late
REMINDER_DAYS_BEFORE = (3, 1, 0)
OVERDUE_EVERY_DAYS = 7  # overdue reminders: the day after the due date, then weekly
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
FOOTER = "\n\n--\n이 메일은 Navitech 회의지원 Agent가 회의 기록을 바탕으로 작성해 자동으로 보냈습니다. 내용이 다르면 회의지원 Agent에 알려 주세요."


def mail_ref(mail_id: int) -> str:
    return f"MAIL-{mail_id}"


# ---------------------------------------------------------------------------
# Queueing
# ---------------------------------------------------------------------------


def _key(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def staff_email(conn: psycopg.Connection, name: str, title: str | None, organization: str | None) -> str | None:
    """The staff · contacts directory's address for a person: the entry with the same name (whose title, when
    both have one, agrees — or is the person's organization); else, for someone the records know only by role
    ("외부 자문위원 (OO회계법인)"), the one contact of that organization holding that role.
    None unless exactly one entry fits."""
    rows = conn.execute(
        "SELECT title, organization, email FROM staff_directory WHERE lower(replace(name, ' ', '')) = %s", (_key(name),)
    ).fetchall()
    if title:
        def agrees(other: str | None) -> bool:
            a, b = _key(title), _key(other)
            return bool(b) and (a in b or b in a)
        rows = [r for r in rows if not r["title"] or agrees(r["title"]) or agrees(r["organization"])]
    if not rows and (organization or title):
        rows = [r for r in conn.execute("SELECT title, email FROM staff_directory WHERE lower(replace(organization, ' ', '')) = %s",
                                        (_key(organization or title),))
                if r["title"] and _key(r["title"]) in _key(name)]
    return rows[0]["email"] if len(rows) == 1 else None


def address_of(conn: psycopg.Connection, person_id: int) -> tuple[str, str | None]:
    """(display label, email) of a directory entry. A missing email the agent looks for itself — the email the
    person registered at login (by name), then the staff · contacts directory — and keeps what it finds."""
    p = conn.execute(f"SELECT {PERSON_LABEL} AS label, p.display_name, p.title, p.organization, p.email FROM people p WHERE p.id = %s",
                     (person_id,)).fetchone()
    if p["email"]:
        return p["label"], p["email"]
    found = lookup_email(p["display_name"])
    email, sub = found if found else (staff_email(conn, p["display_name"], p["title"], p["organization"]), None)
    if email is None:
        return p["label"], None
    taken = sub and conn.execute("SELECT 1 FROM people WHERE keycloak_sub = %s AND id <> %s", (sub, person_id)).fetchone()
    conn.execute("UPDATE people SET email = %s, keycloak_sub = coalesce(keycloak_sub, %s) WHERE id = %s",
                 (email, None if taken else sub, person_id))
    return p["label"], email


def queue(conn: psycopg.Connection, *, kind: str, meeting_id: int, person_id: int, subject: str, body: str,
          created_by: str | None, action_item_id: int | None = None, dedupe_key: str | None = None) -> int | None:
    """One mail to a directory entry, ready to send once its address is found; None when `dedupe_key` was
    queued before or the recipient is a voice the transcript could not name ("화자 A")."""
    who = conn.execute("SELECT display_name FROM people WHERE id = %s", (person_id,)).fetchone()
    if who and unnamed_speaker(who["display_name"]):
        return None
    label, email = address_of(conn, person_id)
    row = conn.execute(
        """INSERT INTO outbox (kind, meeting_id, action_item_id, person_id, to_name, to_email, subject, body, status,
                               approved_by, approved_at, dedupe_key, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (dedupe_key) DO NOTHING RETURNING id""",
        (kind, meeting_id, action_item_id, person_id, label, email, subject, body + FOOTER,
         "approved" if email else "needs_address", AGENT if email else None, datetime.now(timezone.utc) if email else None,
         dedupe_key, created_by),
    ).fetchone()
    return row["id"] if row else None


def _meeting(conn: psycopg.Connection, meeting_id: int) -> dict[str, Any]:
    return conn.execute(
        "SELECT id, code, title, starts_at, ends_at, location, status FROM meetings WHERE id = %s", (meeting_id,)
    ).fetchone()


def queue_minutes_mail(conn: psycopg.Connection, meeting_id: int, record: dict[str, Any], viewer: Viewer, *,
                       corrected: bool) -> list[dict[str, Any]]:
    """Final (or corrected) minutes → every participant; each open Action Item with a person owner → that owner."""
    m = _meeting(conn, meeting_id)
    tag = "회의록 정정" if corrected else "회의록"
    when = f"{record['date']} {record['time']}" if record.get("time") else record["date"]
    lines = [f"{m['title']} ({when}) 회의록 v{record['version']}을 공유드립니다.", "", "요약", record["summary"]]
    decided = [d for d in record["decisions"] if d["kind"] in ("decision", "resolution", "criterion")]
    if decided:
        lines += ["", "결정사항"] + [f"- {d['ref']} {d['statement']}" for d in decided]
    issues = [d for d in record["decisions"] if d["kind"] == "issue"]
    if issues:
        lines += ["", "미결 쟁점"] + [f"- {d['statement']}" for d in issues]
    if record["action_items"]:
        lines += ["", "Action Item"] + [
            f"- {a['id']} {a['text']} (담당 {a['owner'] or '미정'}, 기한 {a['due'] or '미정'})" for a in record["action_items"]
        ]
    body = "\n".join(lines)
    queued = []
    for p in conn.execute("SELECT person_id FROM meeting_participants WHERE meeting_id = %s ORDER BY person_id", (meeting_id,)):
        mail_id = queue(conn, kind="minutes", meeting_id=meeting_id, person_id=p["person_id"],
                        subject=f"[{tag}] {m['title']} ({when})", body=body, created_by=viewer.email)
        if mail_id:
            queued.append(mail_id)
    for a in conn.execute(
        """SELECT id FROM action_items WHERE meeting_id = %s AND owner_person_id IS NOT NULL AND status = ANY(%s) ORDER BY id""",
        (meeting_id, list(OPEN_STATUSES)),
    ):
        mail_id = queue_assignment_mail(conn, a["id"], viewer)
        if mail_id:
            queued.append(mail_id)
    return summarize(conn, queued)


def unit_owned(conn: psycopg.Connection, meeting_id: int, item_ids: list[int] | None = None) -> list[str]:
    """Open Action Items owned by a unit: they get no assignment mail (a unit has no address); say so."""
    rows = conn.execute(
        """SELECT a.code, u.name FROM action_items a JOIN org_units u ON u.id = a.owner_unit_id
           WHERE a.meeting_id = %s AND a.status = ANY(%s) AND (%s::bigint[] IS NULL OR a.id = ANY(%s)) ORDER BY a.id""",
        (meeting_id, list(OPEN_STATUSES), item_ids, item_ids),
    ).fetchall()
    return [f"{r['code']}: 담당이 부서({r['name']})라 업무 배정 메일을 만들지 않았습니다 (담당자를 사람으로 정하면 만들어집니다)."
            for r in rows]


def queue_assignment_mail(conn: psycopg.Connection, item_id: int, viewer: Viewer) -> int | None:
    """An Action Item with a person owner → that owner. Queued again only when the item changed since the
    last notice (owner, due date or text), so a correction that kept it as it was sends nothing."""
    a = conn.execute(
        """SELECT a.id, a.code, a.text, a.due_date, a.owner_person_id, m.id AS meeting_id, m.title, m.starts_at
           FROM action_items a JOIN meetings m ON m.id = a.meeting_id WHERE a.id = %s AND a.owner_person_id IS NOT NULL""",
        (item_id,),
    ).fetchone()
    if a is None:
        return None
    due = a["due_date"].isoformat() if a["due_date"] else "미정"
    body = (f"{a['title']} ({kst_date(a['starts_at'])})에서 다음 업무가 배정되었습니다.\n\n- {a['code']} {a['text']}\n- 기한: {due}\n\n"
            f"진행 상황은 회의지원 Agent에 알려 주세요 (예: \"{a['code']} 진행 중, 1차 산정 완료\").")
    return queue(conn, kind="assignment", meeting_id=a["meeting_id"], person_id=a["owner_person_id"], action_item_id=a["id"],
                 subject=f"[업무 배정] {a['text']} (기한 {due})", body=body, created_by=viewer.email,
                 dedupe_key="assignment:" + hashlib.sha256(f"{a['id']}|{a['owner_person_id']}|{due}|{a['text']}".encode()).hexdigest())


def queue_schedule_mail(conn: psycopg.Connection, meeting_id: int, action: str, viewer: Viewer) -> list[dict[str, Any]]:
    """Notice of a scheduled meeting (create · update · cancel) → every participant. It replaces the meeting's
    earlier notices not sent yet, so nobody gets an invitation after the change or cancellation."""
    conn.execute(
        """UPDATE outbox SET status = 'rejected', last_error = '새 일정 안내로 대체'
           WHERE meeting_id = %s AND kind = 'schedule' AND status IN ('needs_address', 'pending', 'approved')""",
        (meeting_id,),
    )
    m = _meeting(conn, meeting_id)
    when = kst_minute(m["starts_at"]) + (f" ~ {kst_minute(m['ends_at'])[11:]}" if m["ends_at"] else "")
    tag = {"create": "회의 안내", "update": "회의 변경", "cancel": "회의 취소"}[action]
    agenda = [r["title"] for r in conn.execute("SELECT title FROM agenda_items WHERE meeting_id = %s ORDER BY seq", (meeting_id,))]
    lines = [f"{m['title']} 회의가 {'취소되었습니다' if action == 'cancel' else '아래와 같이 예정되어 있습니다'}.", "",
             f"- 회의 id: {m['code']}", f"- 일시: {when} (한국 시간)", f"- 장소: {m['location'] or '미정'}"]
    if agenda and action != "cancel":
        lines += ["", "안건"] + [f"{i}. {t}" for i, t in enumerate(agenda, 1)]
    queued = []
    for p in conn.execute("SELECT person_id FROM meeting_participants WHERE meeting_id = %s ORDER BY person_id", (meeting_id,)):
        mail_id = queue(conn, kind="schedule", meeting_id=meeting_id, person_id=p["person_id"],
                        subject=f"[{tag}] {m['title']} {when}", body="\n".join(lines), created_by=viewer.email)
        if mail_id:
            queued.append(mail_id)
    return summarize(conn, queued)


def queue_reminders(conn: psycopg.Connection, today: date) -> int:
    """Due-date reminders for open Action Items of final minutes whose owner is a person:
    D-3, D-1, the due date, then the day after and weekly while overdue. Idempotent per day
    (dedupe_key); a newer reminder replaces one of the same item not sent yet."""
    rows = conn.execute(
        """SELECT a.id, a.code, a.text, a.due_date, a.owner_person_id, m.id AS meeting_id, m.title, m.starts_at
           FROM action_items a JOIN meetings m ON m.id = a.meeting_id JOIN minutes mi ON mi.meeting_id = m.id AND mi.status = 'final'
           WHERE a.status = ANY(%s) AND a.due_date IS NOT NULL AND a.owner_person_id IS NOT NULL""",
        (list(OPEN_STATUSES),),
    ).fetchall()
    queued = 0
    for a in rows:
        left = (a["due_date"] - today).days
        if left in REMINDER_DAYS_BEFORE:
            tag = "D-day" if left == 0 else f"D-{left}"
            subject = f"[기한 알림] {tag} {a['text']} ({a['due_date'].isoformat()})"
        elif left < 0 and (-left - 1) % OVERDUE_EVERY_DAYS == 0:
            tag = f"overdue+{-left}"
            subject = f"[기한 경과] {-left}일 지남: {a['text']} (기한 {a['due_date'].isoformat()})"
        else:
            continue
        key = f"reminder:{a['id']}:{a['due_date'].isoformat()}:{tag}"
        if conn.execute("SELECT 1 FROM outbox WHERE dedupe_key = %s", (key,)).fetchone():
            continue
        conn.execute(
            """UPDATE outbox SET status = 'rejected', last_error = '새 기한 알림으로 대체'
               WHERE action_item_id = %s AND kind = 'reminder' AND status IN ('needs_address', 'pending', 'approved')""",
            (a["id"],),
        )
        body = (f"{a['title']} ({kst_date(a['starts_at'])})에서 배정된 업무의 기한 알림입니다.\n\n- {a['code']} {a['text']}\n"
                f"- 기한: {a['due_date'].isoformat()}\n\n완료했거나 기한이 바뀌었으면 회의지원 Agent에 알려 주세요.")
        if queue(conn, kind="reminder", meeting_id=a["meeting_id"], person_id=a["owner_person_id"], action_item_id=a["id"],
                 subject=subject, body=body, created_by="scheduler", dedupe_key=key):
            queued += 1
    return queued


def queue_nudge_mail(conn: psycopg.Connection, item_id: int, viewer: Viewer, today: date,
                     note: str | None = None) -> tuple[int | None, str | None]:
    """A reminder someone asks for now (독촉), apart from the daily ones: an open Action Item of final minutes with a
    person owner → that owner, once a day per item. (mail id, None) or (None, why not)."""
    a = conn.execute(
        """SELECT a.id, a.code, a.text, a.status, a.due_date, a.owner_person_id, m.id AS meeting_id, m.title, m.starts_at,
                  mi.status AS minutes
           FROM action_items a JOIN meetings m ON m.id = a.meeting_id JOIN minutes mi ON mi.meeting_id = m.id WHERE a.id = %s""",
        (item_id,),
    ).fetchone()
    if a["status"] not in OPEN_STATUSES:
        return None, f"{a['code']}은 이미 '{a['status']}' 상태라 독촉 메일을 보내지 않았습니다."
    if a["owner_person_id"] is None:
        return None, f"{a['code']}은 담당이 사람이 아니라(부서 또는 미정) 보낼 사람이 없습니다. 담당자를 먼저 정해 주세요."
    if a["minutes"] != "final":
        return None, f"{a['code']}의 회의록이 아직 초안이라 업무가 배정되지 않았습니다. 회의록을 확정하면 업무 배정 메일이 나갑니다."
    due = a["due_date"]
    late = (today - due).days if due and due < today else 0
    due_text = due.isoformat() if due else "미정"
    subject = (f"[업무 독촉] {late}일 지남: {a['text']} (기한 {due_text})" if late
               else f"[업무 확인 요청] {a['text']} (기한 {due_text})")
    body = (f"{a['title']} ({kst_date(a['starts_at'])})에서 배정된 업무의 진행 상황을 확인해 달라는 요청입니다.\n\n"
            f"- {a['code']} {a['text']}\n- 기한: {due_text}{f' ({late}일 지남)' if late else ''}\n"
            + (f"- 요청 내용: {note.strip()}\n" if note and note.strip() else "")
            + f"\n요청한 사람: {viewer.name or viewer.email}\n완료했거나 기한이 바뀌었으면 회의지원 Agent에 알려 주세요.")
    mail_id = queue(conn, kind="reminder", meeting_id=a["meeting_id"], person_id=a["owner_person_id"], action_item_id=a["id"],
                    subject=subject, body=body, created_by=viewer.email, dedupe_key=f"nudge:{a['id']}:{today.isoformat()}")
    return (mail_id, None) if mail_id else (None, f"{a['code']}의 독촉 메일은 오늘 이미 보냈습니다.")


def retry_addresses(conn: psycopg.Connection) -> int:
    """Mail waiting for an address: look each person up again (they may have registered since) and send what is found."""
    addressed = 0
    for r in conn.execute("SELECT DISTINCT person_id FROM outbox WHERE status = 'needs_address' AND person_id IS NOT NULL").fetchall():
        _, email = address_of(conn, r["person_id"])
        if email:
            addressed += conn.execute(
                """UPDATE outbox SET to_email = %s, status = 'approved', approved_by = %s, approved_at = now()
                   WHERE person_id = %s AND status = 'needs_address'""",
                (email, AGENT, r["person_id"]),
            ).rowcount
    return addressed


def summarize(conn: psycopg.Connection, mail_ids: list[int]) -> list[dict[str, Any]]:
    if not mail_ids:
        return []
    rows = conn.execute(
        "SELECT id, kind, to_name, to_email, subject, status FROM outbox WHERE id = ANY(%s) ORDER BY id", (mail_ids,)
    ).fetchall()
    return [{"id": mail_ref(r["id"]), "kind": r["kind"], "to": r["to_name"], "email": r["to_email"], "subject": r["subject"],
             "status": r["status"]} for r in rows]


# ---------------------------------------------------------------------------
# Review (the manage_outbox Tool)
# ---------------------------------------------------------------------------


def _ids(values: list[str] | None) -> list[int]:
    ids = []
    for v in values or []:
        m = re.fullmatch(r"\s*(?:MAIL-)?(\d+)\s*", str(v), re.IGNORECASE)
        if m:
            ids.append(int(m.group(1)))
    return ids


def manage_outbox(
    *,
    store: MeetingStore,
    viewer: Viewer,
    action: str = "list",
    mail_ids: list[str] | None = None,
    meeting_id: str | None = None,
    email: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    if action not in ACTIONS:
        return {"ok": False, "action": action, "error": f"action은 {', '.join(ACTIONS)} 중 하나여야 합니다."}
    with store.connect() as conn:
        where, params = [VISIBLE], {**viewer.scope()}
        ids = _ids(mail_ids)
        if ids:
            where.append("o.id = ANY(%(ids)s)")
            params["ids"] = ids
        if meeting_id:
            where.append("m.code = %(code)s")
            params["code"] = meeting_id.strip()
        if action == "list":
            retry_addresses(conn)
            if not ids:  # not sent yet, failed, or sent in the last week
                where.append("(o.status IN ('needs_address', 'pending', 'approved', 'failed') OR o.sent_at > now() - interval '7 days')")
            rows = conn.execute(
                f"""SELECT o.id, o.kind, o.to_name, o.to_email, o.subject, o.body, o.status, o.last_error, o.created_at, o.sent_at,
                           m.code AS meeting_code
                    FROM outbox o JOIN meetings m ON m.id = o.meeting_id WHERE {' AND '.join(where)} ORDER BY o.id DESC LIMIT 50""",
                params,
            ).fetchall()
            return {"ok": True, "action": action, "matched": len(rows), "smtp_configured": bool(os.environ.get("SMTP_HOST")),
                    "mails": [_listed(r) for r in rows]}

        if not viewer.can_write:
            return {"ok": False, "action": action, "error": "다른 Agent System이 보낸 요청으로는 메일을 다시 보내거나 취소할 수 없습니다."}
        if not ids and not (meeting_id and action in ("resend", "reject")):
            return {"ok": False, "action": action, "error": "mail_ids(예: MAIL-12) 또는 meeting_id가 필요합니다."}
        if action == "set_address" and (len(ids) != 1 or not email or not _EMAIL.match(email.strip())):
            return {"ok": False, "action": action, "error": "set_address에는 메일 하나(mail_ids)와 올바른 email이 필요합니다."}
        rows = conn.execute(
            f"""SELECT o.id, o.status, o.person_id, o.meeting_id FROM outbox o JOIN meetings m ON m.id = o.meeting_id
                WHERE {' AND '.join(where)} ORDER BY o.id FOR UPDATE OF o""",
            params,
        ).fetchall()
        if not rows:
            return {"ok": False, "action": action, "error": "해당하는 메일이 없거나 열람 권한이 없습니다."}
        done, skipped = [], []
        for r in rows:
            if not can_confirm(conn, viewer, r["meeting_id"]):
                return {"ok": False, "action": action, "error": confirm_denied()}
            if action == "resend" and r["status"] in ("pending", "failed"):
                conn.execute("""UPDATE outbox SET status = 'approved', approved_by = %s, approved_at = now(), attempts = 0, last_error = NULL
                                WHERE id = %s""", (viewer.email, r["id"]))
                done.append(r["id"])
            elif action == "reject" and r["status"] in ("needs_address", "pending", "approved", "failed"):
                conn.execute("UPDATE outbox SET status = 'rejected', last_error = %s WHERE id = %s",
                             (f"취소: {note}" if note else "취소", r["id"]))
                done.append(r["id"])
            elif action == "set_address" and r["status"] in ("needs_address", "pending", "approved", "failed"):
                address = email.strip()
                resend = """UPDATE outbox SET to_email = %s, status = 'approved', approved_by = %s, approved_at = now(), attempts = 0,
                                              last_error = NULL WHERE """
                conn.execute(resend + "id = %s", (address, viewer.email, r["id"]))
                if r["person_id"]:  # later mail to the same person uses it too
                    conn.execute("UPDATE people SET email = %s WHERE id = %s", (address, r["person_id"]))
                    conn.execute(resend + "person_id = %s AND status = 'needs_address'", (address, viewer.email, r["person_id"]))
                done.append(r["id"])
            else:
                skipped.append(f"{mail_ref(r['id'])}: '{r['status']}' 상태라 {action} 대상이 아님")
        return {"ok": True, "action": action, "done": [mail_ref(i) for i in done], "skipped": skipped,
                "smtp_configured": bool(os.environ.get("SMTP_HOST")), "mails": summarize(conn, done)}


def _listed(r: dict[str, Any]) -> dict[str, Any]:
    out = {"id": mail_ref(r["id"]), "kind": r["kind"], "meeting_id": r["meeting_code"], "to": r["to_name"], "email": r["to_email"],
           "subject": r["subject"], "status": r["status"], "body": r["body"].removesuffix(FOOTER)}
    if r["sent_at"]:
        out["sent_at"] = kst_minute(r["sent_at"])
    if r["last_error"]:
        out["last_error"] = r["last_error"]
    return out


# ---------------------------------------------------------------------------
# Delivery (the background job)
# ---------------------------------------------------------------------------


def smtp_settings() -> dict[str, Any] | None:
    """SMTP_HOST, SMTP_PORT (587), SMTP_SECURITY starttls | ssl | none, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM."""
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        return None
    security = os.environ.get("SMTP_SECURITY", "starttls").strip().lower()
    return {
        "host": host,
        "port": int(os.environ.get("SMTP_PORT", "465" if security == "ssl" else "587")),
        "security": security,
        "username": os.environ.get("SMTP_USERNAME", "").strip(),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "sender": os.environ.get("SMTP_FROM", "").strip() or os.environ.get("SMTP_USERNAME", "").strip(),
    }


def _connect_smtp(cfg: dict[str, Any]) -> smtplib.SMTP:
    if cfg["security"] == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20)
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=20)
        if cfg["security"] == "starttls":
            server.starttls()
    if cfg["username"]:
        server.login(cfg["username"], cfg["password"])
    return server


def deliver_approved(conn: psycopg.Connection, *, limit: int = 20, connect=_connect_smtp) -> int:
    """Sends approved mail; a failure is retried on the next run up to MAX_ATTEMPTS, then marked failed.
    Mail older than STALE_DAYS (the mail server was set up late) is marked failed instead of sent late."""
    cfg = smtp_settings()
    if cfg is None:
        return 0
    conn.execute(
        "UPDATE outbox SET status = 'failed', last_error = %s WHERE status = 'approved' AND approved_at < now() - make_interval(days => %s)",
        (f"{STALE_DAYS}일 안에 보내지 못해 발송하지 않음 (필요하면 다시 보내기)", STALE_DAYS),
    )
    rows = conn.execute(
        """SELECT id, kind, meeting_id, person_id, to_name, to_email, subject, body, attempts FROM outbox
           WHERE status = 'approved' ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED""",
        (limit,),
    ).fetchall()
    if not rows:
        return 0
    sent, rendered = 0, {}
    try:
        server = connect(cfg)
    except (OSError, smtplib.SMTPException) as e:
        log.warning("SMTP connection failed: %s", e)
        for r in rows:
            _failed(conn, r, f"SMTP 연결 실패: {e}")
        return 0
    try:
        for r in rows:
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = cfg["sender"], Address(display_name=r["to_name"], addr_spec=r["to_email"]), r["subject"]
            msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid(domain="navitech")
            msg.set_content(r["body"])
            if r["kind"] == "minutes":
                for name, content in _documents(conn, r, rendered):
                    msg.add_attachment(content, maintype="application", subtype="pdf", filename=name)
            try:
                server.send_message(msg)
            except smtplib.SMTPException as e:
                _failed(conn, r, str(e))
                continue
            conn.execute("UPDATE outbox SET status = 'sent', sent_at = now(), attempts = attempts + 1, last_error = NULL WHERE id = %s", (r["id"],))
            sent += 1
    finally:
        try:
            server.quit()
        except (OSError, smtplib.SMTPException):
            pass
    return sent


def _documents(conn: psycopg.Connection, r: dict[str, Any], rendered: dict[Any, Any]) -> list[tuple[str, bytes]]:
    """PDFs for a minutes mail: the 회의록 for everyone, and the 회의 결과 보고서 too for the meeting's chair and
    secretary (they report it up). Made once per meeting in a delivery pass; a document that cannot be made
    (no PDF library or fonts) is left out rather than holding the mail back."""
    from .meeting_report import REPORT_ROLES, filename, fonts_available, render, report_data  # imports meeting_minutes → outbox

    if not fonts_available():
        return []
    role = conn.execute("SELECT role FROM meeting_participants WHERE meeting_id = %s AND person_id = %s",
                        (r["meeting_id"], r["person_id"])).fetchone()
    kinds = ["minutes"] + (["report"] if role and role["role"] in REPORT_ROLES else [])
    out = []
    try:
        for kind in kinds:
            if (r["meeting_id"], kind) not in rendered:
                data = rendered.get(r["meeting_id"]) or rendered.setdefault(r["meeting_id"], report_data(conn, r["meeting_id"]))
                rendered[(r["meeting_id"], kind)] = (filename(data, kind), render(data, kind))
            out.append(rendered[(r["meeting_id"], kind)])
    except Exception:
        log.exception("could not make the meeting PDF for %s", mail_ref(r["id"]))
    return out


def _failed(conn: psycopg.Connection, r: dict[str, Any], error: str) -> None:
    status = "failed" if r["attempts"] + 1 >= MAX_ATTEMPTS else "approved"
    conn.execute("UPDATE outbox SET status = %s, attempts = attempts + 1, last_error = %s WHERE id = %s", (status, error[:500], r["id"]))
