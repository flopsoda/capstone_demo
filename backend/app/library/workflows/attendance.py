"""출석 확인(QR)과 참석자 확인 — save_meeting_minutes workflow와 회의 업무 화면(desk)이 쓴다.

녹음만으로는 말하지 않은 참석자를 알 수 없다. 그래서 녹음을 올리면 화면이 출석 확인을 열고(open_session), 화면의 QR에는
30초마다 바뀌는 코드(window_token)가 들어 있다. 참석자가 휴대폰으로 찍으면 회사 계정으로 로그인한 채 출석한다(join).
회의록이 저장되면 출석 확인이 그 회의에 연결되어(link_session) 출석한 사람이 참석자가 된다(apply_checkins) — 이름과
이메일을 알게 되므로 말하지 않은 사람에게도 할 일을 맡기고 메일을 보낼 수 있다.

목소리로 이름을 찾지 못한 발언자('화자 C')는 회의 카드에서 사람을 골라 정한다(assign_speaker). 그 사람이 출석할 때
목소리 학습에 동의했으면 speech-service가 그 녹음의 목소리를 익혀 다음 회의부터 이름으로 적는다(transcripts.learn_voice).
'각자' 하는 할 일은 참석자마다 한 건씩 나눈다(split_item). recall은 이번 참석자들이 이전 회의에서 맡아 아직 열려 있는
일과, 같은 사람들이 있던 회의의 미결 쟁점이다.

Not agent-visible; no LangChain dependency.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from datetime import date, datetime, timezone
from typing import Any

import psycopg

from .meeting_store import (
    OPEN_STATUSES,
    PERSON_LABEL,
    VISIBLE,
    MeetingStore,
    Viewer,
    find_person,
    kst_date,
    next_item_number,
    person_id,
    today_kst,
    unnamed_speaker,
)
from .transcripts import learn_voice

WINDOW_SECONDS = 30  # QR 코드가 바뀌는 간격
ACCEPT_WINDOWS = 4  # 지금 코드와 직전 3개까지 받는다 (찍고 로그인하는 데 걸리는 시간)
SESSION_HOURS = 3  # 출석 확인을 연 뒤 받는 시간
LINK_HOURS = 12  # 출석 확인 코드 없이 녹음 회의록을 저장하면, 이 시간 안에 같은 사람이 연 출석 확인을 붙인다
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE = re.compile(r"^CHK-[A-Z2-9]{6}$")


def checkin_enabled() -> bool:
    """CHECKIN_ENABLED=0 turns QR check-in off (the gateway refuses check-in; saved minutes link no session)."""
    return os.environ.get("CHECKIN_ENABLED", "1").strip().lower() not in ("0", "off", "false", "no")
RECALL_ITEMS = 8
RECALL_ISSUES = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def window_token(secret: str, code: str, window: int) -> str:
    """The code a QR shows during one 30-second window: HMAC of the session's secret, 8 letters."""
    mac = hmac.new(secret.encode(), f"{code}:{window}".encode(), hashlib.sha256).digest()
    return base64.b32encode(mac).decode()[:8]


def current_window(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // WINDOW_SECONDS)


def _token_ok(secret: str, code: str, token: str | None, now: float | None = None) -> bool:
    if not token:
        return False
    w = current_window(now)
    given = token.strip().upper()
    return any(hmac.compare_digest(given, window_token(secret, code, w - k)) for k in range(ACCEPT_WINDOWS))


def _session(conn: psycopg.Connection, code: str | None) -> dict[str, Any] | None:
    code = (code or "").strip().upper()
    if not CODE.match(code):
        return None
    return conn.execute("SELECT * FROM checkin_sessions WHERE code = %s", (code,)).fetchone()


def _attendees(conn: psycopg.Connection, session_id: int) -> list[dict[str, Any]]:
    return conn.execute(
        "SELECT email, name, user_id, checked_in_at, voice_consent_at FROM checkin_attendees WHERE session_id = %s ORDER BY checked_in_at",
        (session_id,),
    ).fetchall()


def _status(conn: psycopg.Connection, s: dict[str, Any], *, with_token: bool, now: float | None = None) -> dict[str, Any]:
    clock = now if now is not None else time.time()
    people = _attendees(conn, s["id"])
    meeting = conn.execute("SELECT code, title FROM meetings WHERE id = %s", (s["meeting_id"],)).fetchone() if s["meeting_id"] else None
    out = {
        "code": s["code"],
        "open": s["closes_at"] > _now(),
        "closes_at": s["closes_at"].isoformat(timespec="minutes"),
        "count": len(people),
        "attendees": [{"name": p["name"], "email": p["email"], "at": p["checked_in_at"].isoformat(timespec="seconds"),
                       "voice_consent": p["voice_consent_at"] is not None} for p in people],
        "meeting": {"id": meeting["code"], "title": meeting["title"]} if meeting else None,
    }
    if with_token and out["open"]:
        out["token"] = window_token(s["secret"], s["code"], current_window(clock))
        out["valid_for"] = WINDOW_SECONDS - int(clock % WINDOW_SECONDS)
    return out


# ---------------------------------------------------------------------------
# 출석 확인: 여는 사람(녹음을 올린 사람)과 출석하는 사람
# ---------------------------------------------------------------------------


def open_session(*, store: MeetingStore, viewer: Viewer, source_name: str | None = None) -> dict[str, Any]:
    if not viewer.can_write or not viewer.email:
        return {"ok": False, "error": "로그인한 사용자만 출석 확인을 열 수 있습니다."}
    with store.connect() as conn:
        while True:
            code = "CHK-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
            row = conn.execute(
                """INSERT INTO checkin_sessions (code, secret, source_name, created_by, closes_at)
                   VALUES (%s, %s, %s, %s, now() + make_interval(hours => %s)) ON CONFLICT (code) DO NOTHING RETURNING *""",
                (code, secrets.token_hex(16), (source_name or "").strip()[:200] or None, viewer.email.lower(), SESSION_HOURS),
            ).fetchone()
            if row:
                return {"ok": True, **_status(conn, row, with_token=True)}


def session_status(*, store: MeetingStore, viewer: Viewer, code: str | None, now: float | None = None) -> dict[str, Any]:
    """QR을 띄운 화면이 묻는 상태: 지금 코드와 출석한 사람들. 연 사람만 코드를 받는다."""
    with store.connect() as conn:
        s = _session(conn, code)
        if s is None or s["created_by"] != (viewer.email or "").lower():
            return {"ok": False, "error": "출석 확인을 찾을 수 없습니다."}
        return {"ok": True, **_status(conn, s, with_token=True, now=now)}


def close_session(*, store: MeetingStore, viewer: Viewer, code: str | None) -> dict[str, Any]:
    with store.connect() as conn:
        s = _session(conn, code)
        if s is None or s["created_by"] != (viewer.email or "").lower():
            return {"ok": False, "error": "출석 확인을 찾을 수 없습니다."}
        conn.execute("UPDATE checkin_sessions SET closes_at = least(closes_at, now()) WHERE id = %s", (s["id"],))
        return {"ok": True, **_status(conn, {**s, "closes_at": min(s["closes_at"], _now())}, with_token=False)}


def join(*, store: MeetingStore, viewer: Viewer, code: str | None, token: str | None,
         voice_consent: bool | None = None, now: float | None = None) -> dict[str, Any]:
    """휴대폰에서 QR을 찍은 사람의 출석. 이미 출석했으면 코드 없이도 목소리 학습 동의만 바꿀 수 있다."""
    if not viewer.email:
        return {"ok": False, "error": "회사 계정으로 로그인해야 출석할 수 있습니다."}
    email = viewer.email.lower()
    with store.connect() as conn:
        s = _session(conn, code)
        if s is None:
            return {"ok": False, "error": "출석 확인을 찾을 수 없습니다. 화면의 QR을 다시 찍어 주세요."}
        joined = conn.execute("SELECT 1 FROM checkin_attendees WHERE session_id = %s AND email = %s", (s["id"], email)).fetchone()
        if not joined:
            if s["closes_at"] <= _now():
                return {"ok": False, "error": "출석 확인이 끝났습니다. 회의록을 올린 사람에게 참석자 추가를 부탁해 주세요."}
            if not _token_ok(s["secret"], s["code"], token, now):
                return {"ok": False, "expired": True, "error": "QR이 바뀌었습니다. 화면의 새 QR을 다시 찍어 주세요."}
        conn.execute(
            """INSERT INTO checkin_attendees (session_id, email, name, user_id, voice_consent_at)
               VALUES (%s, %s, %s, %s, CASE WHEN %s::boolean THEN now() END)
               ON CONFLICT (session_id, email) DO UPDATE SET
                 name = EXCLUDED.name, user_id = coalesce(EXCLUDED.user_id, checkin_attendees.user_id),
                 voice_consent_at = CASE WHEN %s::boolean IS NULL THEN checkin_attendees.voice_consent_at
                                         WHEN %s::boolean THEN coalesce(checkin_attendees.voice_consent_at, now()) END""",
            (s["id"], email, viewer.name or email, viewer.user_id, bool(voice_consent), voice_consent, voice_consent),
        )
        if s["meeting_id"]:  # 회의록이 먼저 저장되었으면 바로 참석자로
            apply_checkins(conn, s["meeting_id"])
        me = conn.execute("SELECT checked_in_at, voice_consent_at FROM checkin_attendees WHERE session_id = %s AND email = %s",
                          (s["id"], email)).fetchone()
        meeting = conn.execute("SELECT title FROM meetings WHERE id = %s", (s["meeting_id"],)).fetchone() if s["meeting_id"] else None
    return {"ok": True, "name": viewer.name or email, "email": email, "code": s["code"],
            "checked_in_at": me["checked_in_at"].isoformat(timespec="seconds"),
            "voice_consent": me["voice_consent_at"] is not None, "source_name": s["source_name"],
            "meeting": meeting["title"] if meeting else None}


# ---------------------------------------------------------------------------
# 회의록에 연결: 출석한 사람 → 참석자
# ---------------------------------------------------------------------------


def link_session(conn: psycopg.Connection, meeting_id: int, viewer: Viewer, code: str | None, *,
                 from_recording: bool, warnings: list[str]) -> int | None:
    """이 회의에 붙일 출석 확인: 요청에 적힌 코드, 없으면 (녹음으로 쓰는 회의록일 때) 같은 사람이 최근 연 것."""
    if not checkin_enabled():
        return None
    if conn.execute("SELECT 1 FROM checkin_sessions WHERE meeting_id = %s", (meeting_id,)).fetchone() and not code:
        return None  # 이미 붙어 있음
    me = (viewer.email or "").lower()
    if code and code.strip():
        s = _session(conn, code)
        if s is None or (s["created_by"] != me and s["meeting_id"] != meeting_id):
            warnings.append(f"출석 확인 '{code.strip()}'을 찾지 못해 출석한 사람을 참석자로 넣지 못함")
            return None
    elif from_recording and me:
        s = conn.execute(
            """SELECT * FROM checkin_sessions WHERE created_by = %s AND meeting_id IS NULL
                 AND created_at > now() - make_interval(hours => %s) ORDER BY created_at DESC LIMIT 1""",
            (me, LINK_HOURS),
        ).fetchone()
        if s is None:
            return None
    else:
        return None
    if s["meeting_id"] and s["meeting_id"] != meeting_id:
        warnings.append(f"출석 확인 {s['code']}은 이미 다른 회의에 연결되어 있어 이 회의에는 붙이지 않음")
        return None
    conn.execute("UPDATE checkin_sessions SET meeting_id = %s WHERE id = %s", (meeting_id, s["id"]))
    return s["id"]


def _account_person(conn: psycopg.Connection, email: str, name: str, user_id: str | None) -> int:
    """회사 계정(이메일 · Keycloak id)의 사람. 없으면 새로 등록한다 (이름은 계정의 이름)."""
    row = None
    if user_id:
        row = conn.execute("SELECT id FROM people WHERE keycloak_sub = %s", (user_id,)).fetchone()
    if row is None:
        row = conn.execute("SELECT id, keycloak_sub FROM people WHERE lower(email) = lower(%s) AND speaker_meeting_id IS NULL ORDER BY id LIMIT 1",
                           (email,)).fetchone()
        if row is not None and user_id and not row["keycloak_sub"] and not conn.execute(
                "SELECT 1 FROM people WHERE keycloak_sub = %s", (user_id,)).fetchone():
            conn.execute("UPDATE people SET keycloak_sub = %s WHERE id = %s", (user_id, row["id"]))
    if row is not None:
        return row["id"]
    return conn.execute("INSERT INTO people (display_name, email, keycloak_sub) VALUES (%s, %s, %s) RETURNING id",
                        (name.strip() or email, email, user_id)).fetchone()["id"]


def _squash(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def apply_checkins(conn: psycopg.Connection, meeting_id: int) -> int:
    """이 회의에 연결된 출석 확인의 출석자를 참석자로 (출석 시각과 함께). 회의록에 이름만 있던 같은 이름의 사람은
    출석한 계정으로 합친다. 넣은 사람 수."""
    rows = conn.execute(
        """SELECT a.email, a.name, a.user_id, min(a.checked_in_at) AS at FROM checkin_attendees a
           JOIN checkin_sessions s ON s.id = a.session_id WHERE s.meeting_id = %s GROUP BY a.email, a.name, a.user_id""",
        (meeting_id,),
    ).fetchall()
    for r in rows:
        pid = _account_person(conn, r["email"], r["name"], r["user_id"])
        conn.execute(
            """INSERT INTO meeting_participants (meeting_id, person_id, role, rsvp, attended, checked_in_at)
               VALUES (%s, %s, 'required', 'accepted', true, %s)
               ON CONFLICT (meeting_id, person_id) DO UPDATE SET attended = true,
                 checked_in_at = coalesce(meeting_participants.checked_in_at, EXCLUDED.checked_in_at)""",
            (meeting_id, pid, r["at"]),
        )
        me = conn.execute("SELECT display_name FROM people WHERE id = %s", (pid,)).fetchone()
        for dup in conn.execute(
            """SELECT p.id, p.display_name FROM meeting_participants mp JOIN people p ON p.id = mp.person_id
               WHERE mp.meeting_id = %s AND p.id <> %s AND p.email IS NULL AND p.keycloak_sub IS NULL AND p.speaker_meeting_id IS NULL""",
            (meeting_id, pid),
        ).fetchall():
            if _squash(dup["display_name"]) == _squash(me["display_name"]):
                merge_person(conn, meeting_id, dup["id"], pid)
    return len(rows)


def _drop_if_unused(conn: psycopg.Connection, pid: int) -> None:
    try:
        with conn.transaction():
            conn.execute("DELETE FROM people WHERE id = %s", (pid,))
    except psycopg.errors.ForeignKeyViolation:
        pass  # 다른 회의 기록이 아직 이 사람을 가리킴


def merge_person(conn: psycopg.Connection, meeting_id: int, old: int | None, new: int, label: str | None = None) -> None:
    """이 회의 안에서 `old`를 가리키던 기록을 모두 `new`로 (참석자 · 할 일 담당 · 발언자 · 반대 의견 · 발표자 · 녹음 발언).
    `label`: 목소리 라벨('화자 C')이면 그 라벨로만 적힌 발언자 · 녹음 발언도 `new`로 확인한다 (`old`가 없어도)."""
    if old is not None and old != new:
        _move_person(conn, meeting_id, old, new)
    if label:
        conn.execute("UPDATE decisions SET speaker_id = %s, speaker_label = NULL WHERE meeting_id = %s AND speaker_id IS NULL AND speaker_label = %s",
                     (new, meeting_id, label))
        conn.execute(
            """UPDATE transcript_segments s SET person_id = %s, speaker_confirmed = true FROM transcripts t
               WHERE t.id = s.transcript_id AND t.meeting_id = %s AND s.speaker_label = %s""",
            (new, meeting_id, label),
        )
    if old is not None and old != new:
        conn.execute("UPDATE people SET title = (SELECT title FROM people WHERE id = %s) WHERE id = %s AND title IS NULL", (old, new))
        _drop_if_unused(conn, old)


def _move_person(conn: psycopg.Connection, meeting_id: int, old: int, new: int) -> None:
    mine = conn.execute("SELECT role FROM meeting_participants WHERE meeting_id = %s AND person_id = %s", (meeting_id, new)).fetchone()
    theirs = conn.execute("SELECT role, attended, checked_in_at FROM meeting_participants WHERE meeting_id = %s AND person_id = %s",
                          (meeting_id, old)).fetchone()
    if theirs:
        if mine:
            conn.execute(
                """UPDATE meeting_participants SET role = CASE WHEN role = 'required' THEN %s ELSE role END,
                          attended = CASE WHEN attended IS TRUE OR %s::boolean IS TRUE THEN true ELSE attended END,
                          checked_in_at = coalesce(checked_in_at, %s)
                   WHERE meeting_id = %s AND person_id = %s""",
                (theirs["role"], theirs["attended"], theirs["checked_in_at"], meeting_id, new),
            )
            conn.execute("DELETE FROM meeting_participants WHERE meeting_id = %s AND person_id = %s", (meeting_id, old))
        else:
            conn.execute("UPDATE meeting_participants SET person_id = %s WHERE meeting_id = %s AND person_id = %s", (new, meeting_id, old))
    conn.execute("UPDATE action_items SET owner_person_id = %s, updated_at = now() WHERE meeting_id = %s AND owner_person_id = %s",
                 (new, meeting_id, old))
    conn.execute("UPDATE decisions SET speaker_id = %s, speaker_label = NULL WHERE meeting_id = %s AND speaker_id = %s", (new, meeting_id, old))
    conn.execute(
        """UPDATE decision_dissents x SET person_id = %s FROM decisions d WHERE d.id = x.decision_id AND d.meeting_id = %s
             AND x.person_id = %s AND NOT EXISTS (SELECT 1 FROM decision_dissents y WHERE y.decision_id = x.decision_id AND y.person_id = %s)""",
        (new, meeting_id, old, new),
    )
    conn.execute("DELETE FROM decision_dissents x USING decisions d WHERE d.id = x.decision_id AND d.meeting_id = %s AND x.person_id = %s",
                 (meeting_id, old))
    conn.execute("UPDATE agenda_items SET presenter_id = %s WHERE meeting_id = %s AND presenter_id = %s", (new, meeting_id, old))
    conn.execute(
        """UPDATE transcript_segments s SET person_id = %s FROM transcripts t
           WHERE t.id = s.transcript_id AND t.meeting_id = %s AND s.person_id = %s""",
        (new, meeting_id, old),
    )


# ---------------------------------------------------------------------------
# 회의 카드의 참석자 확인: 목소리 라벨 → 사람, 참석자 더하기 · 빼기, '각자' 할 일 나누기
# ---------------------------------------------------------------------------


def _meeting(conn: psycopg.Connection, viewer: Viewer, meeting_id: str | None) -> dict[str, Any] | None:
    return conn.execute(
        f"""SELECT m.id, m.code, mi.status AS minutes_status FROM meetings m LEFT JOIN minutes mi ON mi.meeting_id = m.id
            WHERE m.code = %(code)s AND {VISIBLE} FOR UPDATE OF m""",
        {"code": (meeting_id or "").strip(), **viewer.scope()},
    ).fetchone()


def _editable(conn: psycopg.Connection, viewer: Viewer, meeting_id: str | None) -> tuple[dict[str, Any] | None, str | None]:
    if not viewer.can_write:
        return None, "다른 Agent System이 보낸 요청으로는 회의 기록을 바꿀 수 없습니다."
    m = _meeting(conn, viewer, meeting_id)
    if m is None:
        return None, "해당하는 회의가 없거나 열람 권한이 없습니다."
    if m["minutes_status"] == "final":
        return None, "확정된 회의록의 참석자는 회의록 정정으로 바꿔 주세요."
    return m, None


def _target_person(conn: psycopg.Connection, person: dict[str, Any] | None, created: list[str]) -> int | None:
    """화면에서 고른 사람: 회사 계정(이메일)이면 그 계정의 사람, 이름만이면 이름으로 찾거나 새로 등록."""
    person = person or {}
    email = str(person.get("email") or "").strip()
    name = str(person.get("name") or "").strip()
    if email:
        return _account_person(conn, email, name or email, person.get("user_id"))
    if name and not unnamed_speaker(name):
        return person_id(conn, name, created)
    return None


def _label_of(conn: psycopg.Connection, pid: int) -> str:
    return conn.execute(f"SELECT {PERSON_LABEL} AS label FROM people p WHERE p.id = %s", (pid,)).fetchone()["label"]


def assign_speaker(*, store: MeetingStore, viewer: Viewer, meeting_id: str | None, speaker: str | None,
                   person: dict[str, Any] | None) -> dict[str, Any]:
    """'화자 C' = 고른 사람: 이 회의의 그 목소리 발언 · 발언자 · 담당 · 참석자를 그 사람으로 바꾸고, 회의록 글의 라벨도 이름으로.
    그 사람이 출석할 때 목소리 학습에 동의했으면 다음 회의부터 이름으로 적히도록 목소리를 익힌다."""
    label = (speaker or "").strip()
    if not unnamed_speaker(label):
        return {"ok": False, "error": "이름을 정할 발언자는 '화자 A'처럼 목소리로 찾지 못한 라벨이어야 합니다."}
    created: list[str] = []
    with store.connect() as conn:
        m, error = _editable(conn, viewer, meeting_id)
        if error:
            return {"ok": False, "error": error}
        mid = m["id"]
        heard = conn.execute(
            """SELECT t.source_ref, count(*) AS turns, sum(s.end_s - s.start_s) AS seconds FROM transcript_segments s
               JOIN transcripts t ON t.id = s.transcript_id WHERE t.meeting_id = %s AND s.speaker_label = %s
               GROUP BY t.source_ref ORDER BY seconds DESC""",
            (mid, label),
        ).fetchall()
        placeholder = find_person(conn, label, mid)
        if not heard and placeholder is None:
            return {"ok": False, "error": f"이 회의에 '{label}' 발언자가 없습니다."}
        target = _target_person(conn, person, created)
        if target is None:
            return {"ok": False, "error": "누구인지 고르거나 이름을 적어 주세요."}
        merge_person(conn, mid, placeholder, target, label)
        conn.execute(
            """INSERT INTO meeting_participants (meeting_id, person_id, role, rsvp, attended) VALUES (%s, %s, 'required', 'accepted', true)
               ON CONFLICT (meeting_id, person_id) DO UPDATE SET attended = true""",
            (mid, target),
        )
        name = _label_of(conn, target)
        pattern = re.escape(label) + r"(?![A-Z0-9])"
        minutes = conn.execute("SELECT summary, body FROM minutes WHERE meeting_id = %s", (mid,)).fetchone()
        if minutes:
            conn.execute("UPDATE minutes SET summary = %s, body = %s, updated_at = now() WHERE meeting_id = %s",
                         (re.sub(pattern, name, minutes["summary"] or ""), re.sub(pattern, name, minutes["body"]) if minutes["body"] else None, mid))
        who = conn.execute("SELECT email, keycloak_sub FROM people WHERE id = %s", (target,)).fetchone()
        consent = conn.execute(
            """SELECT a.user_id, a.voice_consent_at FROM checkin_attendees a JOIN checkin_sessions s ON s.id = a.session_id
               WHERE s.meeting_id = %s AND lower(a.email) = lower(%s) AND a.voice_consent_at IS NOT NULL ORDER BY a.checked_in_at LIMIT 1""",
            (mid, who["email"] or ""),
        ).fetchone() if who["email"] else None
        code = m["code"]
    voice = "no_consent"
    if consent and heard and heard[0]["source_ref"]:
        voice = learn_voice(heard[0]["source_ref"], label, user_id=consent["user_id"] or who["keycloak_sub"], email=who["email"],
                            name=name, consent_at=consent["voice_consent_at"])
    return {"ok": True, "meeting_id": code, "speaker": label, "person": name, "voice": voice, "new_directory_entries": created}


def add_attendee(*, store: MeetingStore, viewer: Viewer, meeting_id: str | None, person: dict[str, Any] | None) -> dict[str, Any]:
    """말하지 않았고 출석 확인도 하지 않은 참석자를 더한다 (회사 계정 또는 이름)."""
    created: list[str] = []
    with store.connect() as conn:
        m, error = _editable(conn, viewer, meeting_id)
        if error:
            return {"ok": False, "error": error}
        target = _target_person(conn, person, created)
        if target is None:
            return {"ok": False, "error": "더할 사람을 고르거나 이름을 적어 주세요."}
        conn.execute(
            """INSERT INTO meeting_participants (meeting_id, person_id, role, rsvp, attended) VALUES (%s, %s, 'required', 'accepted', true)
               ON CONFLICT (meeting_id, person_id) DO UPDATE SET attended = true""",
            (m["id"], target),
        )
        return {"ok": True, "meeting_id": m["code"], "person": _label_of(conn, target), "new_directory_entries": created}


def remove_attendee(*, store: MeetingStore, viewer: Viewer, meeting_id: str | None, person: dict[str, Any] | None) -> dict[str, Any]:
    """잘못 들어간 참석자를 뺀다 (그 사람이 맡은 할 일이 있으면 담당을 먼저 바꿔야 한다)."""
    name = str((person or {}).get("name") or "").strip()
    email = str((person or {}).get("email") or "").strip()
    with store.connect() as conn:
        m, error = _editable(conn, viewer, meeting_id)
        if error:
            return {"ok": False, "error": error}
        row = conn.execute(
            f"""SELECT p.id FROM meeting_participants mp JOIN people p ON p.id = mp.person_id WHERE mp.meeting_id = %s
                  AND ((%s <> '' AND lower(p.email) = lower(%s)) OR ({PERSON_LABEL}) = %s OR p.display_name = %s) LIMIT 1""",
            (m["id"], email, email, name, name),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "그 참석자를 찾지 못했습니다."}
        if conn.execute("SELECT 1 FROM action_items WHERE meeting_id = %s AND owner_person_id = %s AND status <> 'cancelled'",
                        (m["id"], row["id"])).fetchone():
            return {"ok": False, "error": "이 사람이 맡은 할 일이 있습니다. 담당을 먼저 바꿔 주세요."}
        conn.execute("DELETE FROM meeting_participants WHERE meeting_id = %s AND person_id = %s", (m["id"], row["id"]))
        return {"ok": True, "meeting_id": m["code"]}


def split_item(*, store: MeetingStore, viewer: Viewer, item_id: str | None) -> dict[str, Any]:
    """'각자' 하는 할 일(담당 없음) → 참석자(이름을 아는 사람)마다 한 건씩. 원래 항목은 지운다 (초안만)."""
    with store.connect() as conn:
        if not viewer.can_write:
            return {"ok": False, "error": "다른 Agent System이 보낸 요청으로는 할 일을 바꿀 수 없습니다."}
        item = conn.execute(
            f"""SELECT a.id, a.text, a.due_date, a.owner_person_id, a.owner_unit_id, a.source_transcript_id, a.source_seq,
                       m.id AS mid, m.code AS mcode, mi.status AS minutes_status
                FROM action_items a JOIN meetings m ON m.id = a.meeting_id LEFT JOIN minutes mi ON mi.meeting_id = m.id
                WHERE upper(a.code) = upper(%(code)s) AND {VISIBLE} FOR UPDATE OF a""",
            {"code": (item_id or "").strip(), **viewer.scope()},
        ).fetchone()
        if item is None:
            return {"ok": False, "error": "해당하는 할 일이 없거나 열람 권한이 없습니다."}
        if item["minutes_status"] == "final":
            return {"ok": False, "error": "확정된 회의록의 할 일은 나누지 않습니다. 담당을 지정해 주세요."}
        if item["owner_person_id"] or item["owner_unit_id"]:
            return {"ok": False, "error": "담당이 이미 정해진 할 일입니다."}
        people = conn.execute(
            f"""SELECT p.id, {PERSON_LABEL} AS name FROM meeting_participants mp JOIN people p ON p.id = mp.person_id
                WHERE mp.meeting_id = %s AND mp.attended IS NOT FALSE AND p.speaker_meeting_id IS NULL ORDER BY p.display_name""",
            (item["mid"],),
        ).fetchall()
        if len(people) < 2:
            return {"ok": False, "error": "나눌 참석자가 둘 이상이어야 합니다 (목소리 라벨은 이름을 먼저 정해 주세요)."}
        made = []
        for p in people:
            code = f"{item['mcode']}-A{next_item_number(conn, item['mid'], item['mcode'])}"
            conn.execute(
                """INSERT INTO action_items (code, meeting_id, text, owner_person_id, due_date, source_transcript_id, source_seq)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (code, item["mid"], item["text"], p["id"], item["due_date"], item["source_transcript_id"], item["source_seq"]),
            )
            made.append({"id": code, "owner": p["name"]})
        conn.execute("DELETE FROM action_items WHERE id = %s", (item["id"],))
        return {"ok": True, "meeting_id": item["mcode"], "items": made}


# ---------------------------------------------------------------------------
# 보기: 참석자 확인 (카드 · 회의록 저장 결과)과 이전 회의에서 이어지는 일
# ---------------------------------------------------------------------------


def attendance_view(conn: psycopg.Connection, meeting_id: int) -> dict[str, Any]:
    """누가 출석했고(QR) 누가 말했는지(목소리), 이름을 정해야 할 목소리, 말하지 않은 참석자."""
    people = conn.execute(
        f"""SELECT p.id, {PERSON_LABEL} AS name, p.email, mp.role, mp.attended, mp.checked_in_at, p.speaker_meeting_id IS NOT NULL AS voice_label,
                   (SELECT count(*) FROM transcript_segments s JOIN transcripts t ON t.id = s.transcript_id
                     WHERE t.meeting_id = mp.meeting_id AND s.person_id = p.id) AS turns
            FROM meeting_participants mp JOIN people p ON p.id = mp.person_id WHERE mp.meeting_id = %s
            ORDER BY p.speaker_meeting_id IS NOT NULL, p.display_name""",
        (meeting_id,),
    ).fetchall()
    voices = conn.execute(
        """SELECT s.speaker_label AS label, count(*) AS turns, round(sum(s.end_s - s.start_s)) AS seconds,
                  (array_agg(s.text ORDER BY s.start_s))[1] AS first_text, min(s.start_s) AS first_at
           FROM transcript_segments s JOIN transcripts t ON t.id = s.transcript_id
           WHERE t.meeting_id = %s AND s.person_id IS NULL GROUP BY s.speaker_label ORDER BY min(s.start_s)""",
        (meeting_id,),
    ).fetchall()
    unknown = [v for v in voices if unnamed_speaker(v["label"])]
    recorded = conn.execute("SELECT 1 FROM transcripts WHERE meeting_id = %s", (meeting_id,)).fetchone() is not None
    named = [p for p in people if not p["voice_label"]]
    silent = [p for p in named if recorded and p["turns"] == 0 and p["attended"] is not False]
    session = conn.execute("SELECT code, closes_at FROM checkin_sessions WHERE meeting_id = %s ORDER BY id DESC LIMIT 1",
                           (meeting_id,)).fetchone()
    suggestion = None
    if len(unknown) == 1:  # 이름 모를 목소리가 하나, 출석했지만 목소리가 없는 사람이 하나면 그 사람일 가능성이 높다
        quiet = [p for p in silent if p["checked_in_at"]]
        if len(quiet) == 1:
            suggestion = {"speaker": unknown[0]["label"], "name": quiet[0]["name"], "email": quiet[0]["email"]}

    def clock(seconds: float | None) -> str:
        s = int(seconds or 0)
        return f"{s // 60:02d}:{s % 60:02d}"

    return {
        "recorded": recorded,
        "checkin": {"code": session["code"], "open": session["closes_at"] > _now()} if session else None,
        "people": [{"name": p["name"], "email": p["email"], "role": p["role"], "checked_in": p["checked_in_at"] is not None,
                    "spoke": p["turns"] > 0, "attended": p["attended"]} for p in named],
        "unknown_speakers": [{"label": v["label"], "turns": v["turns"], "seconds": int(v["seconds"] or 0),
                              "first": f"[{clock(v['first_at'])}] {(v['first_text'] or '')[:80]}"} for v in unknown],
        "silent": [p["name"] for p in silent],
        "suggestion": suggestion,
    }


def recall(conn: psycopg.Connection, meeting_id: int, viewer: Viewer, today: date | None = None) -> dict[str, Any]:
    """이전 회의에서 이어지는 일: 이번 참석자들이 맡아 아직 열린 할 일(기한 지난 것 먼저)과, 이번 참석자가 있던
    이전 회의의 미결 쟁점. 이 회의의 주제로 열린 이전 회의가 있으면 그 주제 안에서만 찾는다 (같은 사람이 다른 주제에서
    남긴 일은 끌어오지 않는다)."""
    today = today or today_kst()
    m = conn.execute("SELECT starts_at, subject FROM meetings WHERE id = %s", (meeting_id,)).fetchone()
    params = {**viewer.scope(), "mid": meeting_id, "starts": m["starts_at"], "today": today, "statuses": list(OPEN_STATUSES),
              "subject": m["subject"]}
    same = m["subject"] and conn.execute(
        "SELECT 1 FROM meetings WHERE subject = %s AND id <> %s AND starts_at <= %s LIMIT 1", (m["subject"], meeting_id, m["starts_at"]),
    ).fetchone()
    topic = "AND m.subject = %(subject)s" if same else ""
    attendees = """SELECT mp.person_id FROM meeting_participants mp JOIN people p ON p.id = mp.person_id
                   WHERE mp.meeting_id = %(mid)s AND p.speaker_meeting_id IS NULL"""
    items = conn.execute(
        f"""SELECT a.code, a.text, a.due_date, a.status, {PERSON_LABEL} AS owner, m.code AS mcode, m.title, m.starts_at
            FROM action_items a JOIN meetings m ON m.id = a.meeting_id JOIN people p ON p.id = a.owner_person_id
            WHERE a.meeting_id <> %(mid)s AND m.starts_at <= %(starts)s AND a.status = ANY(%(statuses)s)
              AND a.owner_person_id IN ({attendees}) {topic} AND {VISIBLE}
            ORDER BY (a.due_date IS NOT NULL AND a.due_date < %(today)s) DESC, a.due_date NULLS LAST, m.starts_at DESC
            LIMIT {RECALL_ITEMS}""",
        params,
    ).fetchall()
    issues = conn.execute(
        f"""SELECT d.seq, d.statement, m.code AS mcode, m.title, m.starts_at FROM decisions d JOIN meetings m ON m.id = d.meeting_id
            WHERE d.kind = 'issue' AND d.resolved_by_id IS NULL AND d.meeting_id <> %(mid)s AND m.starts_at <= %(starts)s
              AND EXISTS (SELECT 1 FROM meeting_participants mp WHERE mp.meeting_id = m.id AND mp.person_id IN ({attendees}))
              {topic} AND {VISIBLE}
            ORDER BY m.starts_at DESC, d.seq LIMIT {RECALL_ISSUES}""",
        params,
    ).fetchall()
    return {
        "open_items": [{"id": r["code"], "text": r["text"], "owner": r["owner"], "status": r["status"],
                        "due": r["due_date"].isoformat() if r["due_date"] else None,
                        "overdue_days": (today - r["due_date"]).days if r["due_date"] and r["due_date"] < today else 0,
                        "meeting": {"id": r["mcode"], "title": r["title"], "date": kst_date(r["starts_at"])}} for r in items],
        "open_issues": [{"ref": f"{r['mcode']}-D{r['seq']}", "statement": r["statement"],
                         "meeting": {"id": r["mcode"], "title": r["title"], "date": kst_date(r["starts_at"])}} for r in issues],
    }


def attendance_for(*, store: MeetingStore, viewer: Viewer, meeting_id: str | None, today: date | None = None) -> dict[str, Any] | None:
    with store.connect() as conn:
        m = conn.execute(f"SELECT m.id FROM meetings m WHERE m.code = %(code)s AND {VISIBLE}",
                         {"code": (meeting_id or "").strip(), **viewer.scope()}).fetchone()
        if m is None:
            return None
        return {"attendance": attendance_view(conn, m["id"]), "recall": recall(conn, m["id"], viewer, today)}

