"""Progress reported from the mail itself: the buttons in an assignment · reminder mail (완료 · 진행 중 · 막힘).

Each button links to the gateway's page `<MEETINGS_ACTION_URL>/<token>?s=<status>`, which opens without a login
(like the one-click buttons task tools put in their mail). The token names one Action Item and the person the
mail went to, expires after ACTION_DAYS and is signed with a key derived from PRINCIPAL_SIGNING_KEY (the secret
this system already shares with the gateway). Opening the link only shows the item — mail security scanners open
links on their own — and the change is made when the person presses 보내기 on that page (POST). The link stops
working once the item has another owner. The change is kept in action_item_updates like any other ("메일로 보고"),
by the owner's email. Without MEETINGS_ACTION_URL or the key, mail goes out without buttons.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import os
import time
from datetime import date
from typing import Any

from .meeting_store import MeetingStore, kst_date, today_kst

ACTION_DAYS = 30
STATUSES = {"done": "완료", "in_progress": "진행 중", "blocked": "막힘"}
BUTTONS = (("done", "완료했습니다", "#0f9d58", "#ffffff"), ("in_progress", "진행 중입니다", "#5b5bd6", "#ffffff"),
           ("blocked", "막혔습니다", "#ffffff", "#c2410c"))
ITEM_LABEL = {"open": "미착수", "in_progress": "진행 중", "blocked": "막힘", "done": "완료", "cancelled": "취소"}
VIA = "메일로 보고"
NOTE_LIMIT = 500


def _key() -> bytes | None:
    secret = os.environ.get("PRINCIPAL_SIGNING_KEY", "").strip()
    return hmac.new(secret.encode(), b"navitech mail action", hashlib.sha256).digest() if secret else None


def base_url() -> str | None:
    url = os.environ.get("MEETINGS_ACTION_URL", "").strip().rstrip("/")
    return url if url and _key() else None


def _sign(payload: str, key: bytes) -> str:
    return base64.urlsafe_b64encode(hmac.new(key, payload.encode(), hashlib.sha256).digest()[:16]).decode().rstrip("=")


def make_token(item_id: int, person_id: int, *, now: float | None = None) -> str:
    key = _key()
    if key is None:
        raise RuntimeError("PRINCIPAL_SIGNING_KEY가 없어 메일 버튼을 만들 수 없습니다.")
    payload = f"{item_id}.{person_id}.{int(now or time.time()) + ACTION_DAYS * 86400}"
    return f"{payload}.{_sign(payload, key)}"


def read_token(token: str, *, now: float | None = None) -> tuple[int, int] | str:
    """(item id, person id), or why the link cannot be used."""
    key = _key()
    parts = (token or "").strip().split(".")
    if key is None or len(parts) != 4 or not all(p.isdigit() for p in parts[:3]):
        return "링크가 올바르지 않습니다. 메일의 버튼을 다시 눌러 주세요."
    payload = ".".join(parts[:3])
    if not hmac.compare_digest(parts[3], _sign(payload, key)):
        return "링크가 올바르지 않습니다. 메일의 버튼을 다시 눌러 주세요."
    if int(parts[2]) < (now or time.time()):
        return f"링크를 쓸 수 있는 기간({ACTION_DAYS}일)이 지났습니다. 회의지원 Agent나 회의 업무 화면에서 알려 주세요."
    return int(parts[0]), int(parts[1])


def links(item_id: int, person_id: int) -> dict[str, str] | None:
    """The buttons' addresses for one owner's mail, or None when buttons are off."""
    url = base_url()
    if url is None:
        return None
    token = make_token(item_id, person_id)
    return {status: f"{url}/{token}?s={status}" for status in STATUSES}


# ---------------------------------------------------------------------------
# The mail
# ---------------------------------------------------------------------------


def text_block(urls: dict[str, str]) -> str:
    lines = "\n".join(f"- {STATUSES[s]}: {u}" for s, u in urls.items())
    return f"\n\n진행 상황 알리기 (로그인 없이, {ACTION_DAYS}일 동안):\n{lines}"


def _paragraphs(text: str) -> str:
    return "".join(f'<p style="margin:0 0 14px">{html.escape(p).replace(chr(10), "<br>")}</p>' for p in text.strip().split("\n\n") if p.strip())


def html_mail(body: str, urls: dict[str, str], footer: str) -> str:
    """The same mail as HTML with the buttons (links styled as buttons work in every mail program; forms do not)."""
    font = "font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic','Noto Sans KR',sans-serif"
    buttons = "".join(
        f'<a href="{html.escape(urls[s])}" style="display:inline-block;margin:0 8px 8px 0;padding:11px 18px;border-radius:10px;'
        f'background:{bg};color:{fg};border:1px solid {fg if bg == "#ffffff" else bg};text-decoration:none;font-weight:700;font-size:15px;{font}">'
        f"{label}</a>"
        for s, label, bg, fg in BUTTONS)
    return (
        f'<!doctype html><html lang="ko"><body style="margin:0;padding:0;background:#f5f5f7">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f7"><tr><td align="center" style="padding:24px 12px">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border:1px solid #e5e7eb;border-radius:14px">'
        f'<tr><td style="padding:24px 24px 6px;font-size:15px;line-height:1.6;color:#111827;{font}">{_paragraphs(body)}</td></tr>'
        f'<tr><td style="padding:4px 24px 0;font-size:14px;font-weight:700;color:#111827;{font}">진행 상황을 바로 알려 주세요</td></tr>'
        f'<tr><td style="padding:10px 24px 2px">{buttons}</td></tr>'
        f'<tr><td style="padding:0 24px 22px;font-size:13px;line-height:1.5;color:#6b7280;{font}">로그인 없이 열립니다. 열린 화면에서 메모를 덧붙여 보내면 '
        f"회의 기록에 남고 간사에게 바로 보입니다. 버튼은 {ACTION_DAYS}일 동안 쓸 수 있습니다.</td></tr>"
        f'</table><p style="max-width:560px;margin:14px auto 0;font-size:12px;line-height:1.5;color:#9ca3af;{font}">'
        f"{html.escape(footer.strip().lstrip('-').strip())}</p></td></tr></table></body></html>"
    )


# ---------------------------------------------------------------------------
# The page behind the buttons (gateway /meetings/act/<token> → /api/mail-action)
# ---------------------------------------------------------------------------


def _shown(row: dict[str, Any], today: date) -> dict[str, Any]:
    due = row["due_date"]
    late = (today - due).days if due and due < today and row["status"] != "done" else 0
    return {"id": row["code"], "text": row["text"], "status": row["status"], "status_label": ITEM_LABEL.get(row["status"], row["status"]),
            "due": due.isoformat() if due else None, "late_days": late, "owner": row["display_name"],
            "meeting": {"id": row["meeting_code"], "title": row["title"], "date": kst_date(row["starts_at"])}}


def act(*, store: MeetingStore, token: str, status: str | None = None, note: str | None = None,
        today: date | None = None) -> dict[str, Any]:
    """Without `status`: the item the link names (to show). With it: the owner's report — done · in_progress ·
    blocked (blocked needs a note saying why). {ok, item, changed?, owner_email?} or {ok: False, error}."""
    today = today or today_kst()
    parsed = read_token(token)
    if isinstance(parsed, str):
        return {"ok": False, "error": parsed}
    item_id, person_id = parsed
    with store.connect() as conn:
        row = conn.execute(
            """SELECT a.id, a.code, a.text, a.status, a.due_date, a.owner_person_id, m.code AS meeting_code, m.title, m.starts_at,
                      p.display_name, p.email
               FROM action_items a JOIN meetings m ON m.id = a.meeting_id JOIN people p ON p.id = %s
               WHERE a.id = %s FOR UPDATE OF a""",
            (person_id, item_id),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "이 업무를 찾을 수 없습니다. 회의록이 정정되었을 수 있습니다."}
        if row["owner_person_id"] != person_id:
            return {"ok": False, "error": "이 업무의 담당자가 바뀌어 이 링크로는 진행 상황을 알릴 수 없습니다."}
        item = _shown(row, today)
        if row["status"] == "cancelled":
            return {"ok": False, "item": item, "error": "취소된 업무입니다."}
        if status is None:
            return {"ok": True, "item": item, "statuses": STATUSES}
        if status not in STATUSES:
            return {"ok": False, "item": item, "error": "완료 · 진행 중 · 막힘 가운데 하나를 골라 주세요."}
        note = (note or "").strip()[:NOTE_LIMIT]
        if status == "blocked" and not note:
            return {"ok": False, "item": item, "error": "무엇 때문에 막혔는지 메모에 적어 주세요."}
        changed = status != row["status"]
        if changed:
            conn.execute("UPDATE action_items SET status = %s, completed_at = CASE WHEN %s = 'done' THEN now() END, updated_at = now() WHERE id = %s",
                         (status, status, item_id))
        if changed or note:
            conn.execute(
                "INSERT INTO action_item_updates (action_item_id, by_email, old_status, new_status, note) VALUES (%s, %s, %s, %s, %s)",
                (item_id, row["email"], row["status"], status, f"{VIA}: {note}" if note else VIA),
            )
        shown = {**item, "status": status, "status_label": STATUSES[status], "late_days": 0 if status == "done" else item["late_days"]}
        return {"ok": True, "item": shown, "changed": changed, "noted": bool(note), "owner_email": row["email"]}
