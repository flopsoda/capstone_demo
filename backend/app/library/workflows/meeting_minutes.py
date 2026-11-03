"""Meeting minutes — implementation workflow behind the save_meeting_minutes Tool.

Draft → final. A draft is rewritten freely; finalizing needs every Action Item to have an
owner and a due date, no open Compliance flag (compliance.py), and a requester who may confirm
the meeting's records (meeting_store.can_confirm). Final minutes are never overwritten in place:
a correction (same permission) needs a reason, snapshots the previous version into
minutes_revisions and keeps every existing Action Item (they are closed with update_action_item,
not deleted). A recording's transcript is attached by its speech-service id (transcripts.py);
decisions and Action Items may point at the statement they came from by its [mm:ss] stamp.
Finalizing or correcting queues the minutes and assignment mail for approval (outbox.py).
People who checked in with the QR shown while the recording was uploaded become attendees, silent ones
included (attendance.py); the result tells the model who checked in, which voices still need a name and
what earlier meetings left open for these attendees.

The model supplies the content; ids, codes, validation, the directory lookups and the
access check are deterministic here. Not agent-visible; no LangChain dependency.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .attendance import apply_checkins, attendance_view, link_session, recall
from .compliance import apply_reviews, flags_of, open_flag_count, scan_minutes, scan_transcript
from .meeting_store import (
    DEFAULT_MEETING_TIME,
    KST,
    OWNER_LABEL,
    PERSON_LABEL,
    VISIBLE,
    MeetingStore,
    Viewer,
    can_confirm,
    confirm_denied,
    decision_ref,
    find_decision,
    find_person,
    kst_date,
    new_meeting_code,
    time_label,
    next_item_number,
    parse_date,
    parse_person_label,
    parse_time_of_day,
    person_id,
    resolve_owner,
    unnamed_speaker,
)
from .outbox import queue_minutes_mail, unit_owned
from .transcripts import TranscriptUnavailable, attach_transcript, fetch_transcript, segment_at

ROLES = ("chair", "secretary", "required", "optional", "observer")
KINDS = ("decision", "resolution", "criterion", "opinion", "issue")
OUTCOMES = ("approved", "amended", "rejected", "deferred")
POSITIONS = ("against", "abstain", "recused")
UNKNOWN_SPEAKER = "발언자 확인 필요"


def _fail(error: str) -> dict[str, Any]:
    return {"saved": False, "error": error}


def minutes_record(conn: psycopg.Connection, meeting_id: int) -> dict[str, Any]:
    """The meeting's minutes as one document (also the snapshot kept for each corrected version)."""
    m = conn.execute(
        """SELECT m.code, m.title, m.starts_at, m.ends_at, m.location, m.topics, m.classification,
                  mi.status, mi.version, mi.summary, mi.body, mi.finalized_by, mi.finalized_at
           FROM meetings m JOIN minutes mi ON mi.meeting_id = m.id WHERE m.id = %s""",
        (meeting_id,),
    ).fetchone()
    attendees = conn.execute(
        f"""SELECT {PERSON_LABEL} AS name, mp.role, mp.attended FROM meeting_participants mp JOIN people p ON p.id = mp.person_id
            WHERE mp.meeting_id = %s ORDER BY array_position(%s::text[], mp.role), p.display_name""",
        (meeting_id, list(ROLES)),
    ).fetchall()
    decisions = conn.execute(
        f"""SELECT d.id, d.seq, d.kind, coalesce({PERSON_LABEL}, d.speaker_label) AS speaker, d.statement, d.rationale,
                   d.outcome, d.votes_for, d.votes_against, d.votes_abstain,
                   (SELECT m2.code || '-D' || s.seq FROM decisions s JOIN meetings m2 ON m2.id = s.meeting_id WHERE s.id = d.supersedes_id) AS supersedes
            FROM decisions d LEFT JOIN people p ON p.id = d.speaker_id WHERE d.meeting_id = %s ORDER BY d.seq""",
        (meeting_id,),
    ).fetchall()
    dissents = conn.execute(
        f"""SELECT dd.decision_id, {PERSON_LABEL} AS name, dd.position, dd.reason FROM decision_dissents dd
            JOIN people p ON p.id = dd.person_id JOIN decisions d ON d.id = dd.decision_id WHERE d.meeting_id = %s""",
        (meeting_id,),
    ).fetchall()
    items = conn.execute(
        f"""SELECT a.code, a.text, {OWNER_LABEL} AS owner, a.due_date, a.status FROM action_items a
            LEFT JOIN people p ON p.id = a.owner_person_id LEFT JOIN org_units u ON u.id = a.owner_unit_id
            WHERE a.meeting_id = %s ORDER BY a.id""",
        (meeting_id,),
    ).fetchall()

    def decision(d: dict[str, Any]) -> dict[str, Any]:
        out = {"ref": decision_ref(m["code"], d["seq"]), "kind": d["kind"], "speaker": d["speaker"], "statement": d["statement"]}
        for key in ("rationale", "outcome", "votes_for", "votes_against", "votes_abstain", "supersedes"):
            if d[key] is not None:
                out[key] = d[key]
        own = [{"name": x["name"], "position": x["position"], "reason": x["reason"]} for x in dissents if x["decision_id"] == d["id"]]
        if own:
            out["dissents"] = own
        return out

    return {
        "id": m["code"],
        "date": kst_date(m["starts_at"]),
        "time": time_label(m["starts_at"], m["ends_at"]),
        "title": m["title"],
        "status": m["status"],
        "version": m["version"],
        "classification": m["classification"],
        "location": m["location"],
        "topics": list(m["topics"]),
        "summary": m["summary"],
        "body": m["body"],
        "attendees": [{"name": a["name"], "role": a["role"], "attended": a["attended"]} for a in attendees],
        "decisions": [decision(d) for d in decisions],
        "action_items": [
            {"id": a["code"], "text": a["text"], "owner": a["owner"], "due": a["due_date"].isoformat() if a["due_date"] else None,
             "status": a["status"]}
            for a in items
        ],
        "finalized_by": m["finalized_by"],
        "finalized_at": m["finalized_at"].isoformat() if m["finalized_at"] else None,
    }


def _missing_for_final(conn: psycopg.Connection, meeting_id: int) -> list[str]:
    rows = conn.execute(
        """SELECT a.code, a.text, a.owner_person_id, a.owner_unit_id, a.due_date, p.display_name AS owner_name
           FROM action_items a LEFT JOIN people p ON p.id = a.owner_person_id
           WHERE a.meeting_id = %s AND a.status <> 'cancelled' ORDER BY a.id""",
        (meeting_id,),
    ).fetchall()
    missing = []
    for r in rows:
        if r["owner_person_id"] is None and r["owner_unit_id"] is None:
            missing.append(f"{r['code']} '{r['text']}': 담당자 없음")
        elif unnamed_speaker(r["owner_name"]):  # "화자 A": nobody to send the task to yet
            missing.append(f"{r['code']} '{r['text']}': 담당 '{r['owner_name']}'의 실제 담당자 지정 필요")
        if r["due_date"] is None:
            missing.append(f"{r['code']} '{r['text']}': 기한 없음")
    return missing


def _write_participants(conn: psycopg.Connection, meeting_id: int, attendees: list[dict[str, Any]], created: list[str]) -> None:
    conn.execute("DELETE FROM meeting_participants WHERE meeting_id = %s", (meeting_id,))
    for a in attendees:
        role = a.get("role") or "required"
        if role not in ROLES:
            role = "required"
        if role == "required":
            role = parse_person_label(a["name"])[2] or role
        pid = person_id(conn, a["name"], created, title=a.get("title"), meeting_id=meeting_id)
        attended = a.get("attended", True)
        conn.execute(
            """INSERT INTO meeting_participants (meeting_id, person_id, role, rsvp, attended) VALUES (%s, %s, %s, 'accepted', %s)
               ON CONFLICT (meeting_id, person_id) DO UPDATE SET role = EXCLUDED.role, attended = EXCLUDED.attended""",
            (meeting_id, pid, role, attended),
        )


def _write_decisions(conn: psycopg.Connection, meeting_id: int, decisions: list[dict[str, Any]], viewer: Viewer,
                     created: list[str], warnings: list[str]) -> None:
    """Rewritten in place by position (seq), so other meetings' links to these decisions survive a correction."""
    for seq, d in enumerate(decisions, 1):
        kind = d.get("kind") or "decision"
        if kind not in KINDS:
            warnings.append(f"결정 {seq}: 알 수 없는 종류 '{kind}' → decision으로 저장")
            kind = "decision"
        speaker = (d.get("speaker") or "").strip() or None
        speaker_id = find_person(conn, speaker) if speaker and not unnamed_speaker(speaker) else None
        label = None if speaker_id else speaker
        if kind in ("criterion", "opinion") and not speaker:
            label = UNKNOWN_SPEAKER
            warnings.append(f"결정 {seq}: 발언자 없음 ('{UNKNOWN_SPEAKER}'로 저장)")
        is_resolution = kind == "resolution"
        outcome = d.get("outcome") if is_resolution and d.get("outcome") in OUTCOMES else None
        if is_resolution and outcome is None:
            warnings.append(f"결정 {seq}: 의결 결과(outcome) 없음")
        supersedes = find_decision(conn, d["supersedes"], viewer) if d.get("supersedes") else None
        if d.get("supersedes") and supersedes is None:
            warnings.append(f"결정 {seq}: 바꾼다는 이전 결정 '{d['supersedes']}'을 찾지 못함")
        source = _source(conn, meeting_id, d.get("source_time"), f"결정 {seq}", warnings) or (None, None)
        decision_id = conn.execute(
            """INSERT INTO decisions (meeting_id, seq, kind, speaker_id, speaker_label, statement, rationale, outcome,
                                      votes_for, votes_against, votes_abstain, supersedes_id, source_transcript_id, source_seq)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (meeting_id, seq) DO UPDATE SET
                 kind = EXCLUDED.kind, speaker_id = EXCLUDED.speaker_id, speaker_label = EXCLUDED.speaker_label,
                 statement = EXCLUDED.statement, rationale = EXCLUDED.rationale, outcome = EXCLUDED.outcome,
                 votes_for = EXCLUDED.votes_for, votes_against = EXCLUDED.votes_against,
                 votes_abstain = EXCLUDED.votes_abstain, supersedes_id = EXCLUDED.supersedes_id,
                 source_transcript_id = coalesce(EXCLUDED.source_transcript_id, decisions.source_transcript_id),
                 source_seq = CASE WHEN EXCLUDED.source_transcript_id IS NULL THEN decisions.source_seq ELSE EXCLUDED.source_seq END,
                 resolved_by_id = CASE WHEN EXCLUDED.kind = 'issue' THEN decisions.resolved_by_id END
               RETURNING id""",
            (meeting_id, seq, kind, speaker_id, label, d["statement"], d.get("rationale"), outcome,
             d.get("votes_for") if is_resolution else None, d.get("votes_against") if is_resolution else None,
             d.get("votes_abstain") if is_resolution else None, supersedes, *source),
        ).fetchone()["id"]
        conn.execute("DELETE FROM decision_dissents WHERE decision_id = %s", (decision_id,))
        for x in d.get("dissents") or []:
            position = x.get("position") if x.get("position") in POSITIONS else "against"
            conn.execute(
                "INSERT INTO decision_dissents (decision_id, person_id, position, reason) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (decision_id, person_id(conn, x["name"], created, meeting_id=meeting_id), position, x.get("reason")),
            )
        if d.get("resolves"):
            issue = find_decision(conn, d["resolves"], viewer)
            if issue is None:
                warnings.append(f"결정 {seq}: 해결한다는 쟁점 '{d['resolves']}'을 찾지 못함")
            else:
                conn.execute("UPDATE decisions SET resolved_by_id = %s WHERE id = %s AND kind = 'issue'", (decision_id, issue))
    # Positions past the new list: unlink them from other records first, then drop them.
    stale = [r["id"] for r in conn.execute("SELECT id FROM decisions WHERE meeting_id = %s AND seq > %s", (meeting_id, len(decisions)))]
    if stale:
        conn.execute("UPDATE decisions SET supersedes_id = NULL WHERE supersedes_id = ANY(%s)", (stale,))
        conn.execute("UPDATE decisions SET resolved_by_id = NULL WHERE resolved_by_id = ANY(%s)", (stale,))
        conn.execute("DELETE FROM decisions WHERE id = ANY(%s)", (stale,))


def _source(conn: psycopg.Connection, meeting_id: int, stamp: str | None, what: str, warnings: list[str]) -> tuple[int, int] | None:
    """(transcript_id, seq) of the statement a decision or Action Item cites by its [mm:ss] stamp.
    A meeting without an attached transcript has nothing to cite: the stamp is ignored, not warned about."""
    if not stamp or not conn.execute("SELECT 1 FROM transcripts WHERE meeting_id = %s", (meeting_id,)).fetchone():
        return None
    found = segment_at(conn, meeting_id, stamp)
    if found is None:
        warnings.append(f"{what}: 근거 발언 시각 '{stamp}'에 해당하는 transcript 발언을 찾지 못함")
    return found


def _write_action_items(conn: psycopg.Connection, meeting_id: int, code: str, items: list[dict[str, Any]], *,
                        keep_unlisted: bool, created: list[str], warnings: list[str]) -> None:
    rows = conn.execute("SELECT id, code, text FROM action_items WHERE meeting_id = %s ORDER BY id", (meeting_id,)).fetchall()
    existing = {r["code"]: r["id"] for r in rows}
    # A model re-sending the list often drops the ids: the same text is the same item, so its code stays.
    by_text: dict[str, str] = {}
    for r in rows:
        by_text.setdefault(" ".join(r["text"].split()).lower(), r["code"])
    listed: set[str] = set()
    for n, a in enumerate(items, 1):
        try:
            due = parse_date(a.get("due"))
        except ValueError:
            warnings.append(f"Action Item {n}: 기한 '{a.get('due')}'을 읽을 수 없음 (YYYY-MM-DD)")
            due = None
        owner_person, owner_unit = resolve_owner(conn, a.get("owner"), created, meeting_id)
        source = _source(conn, meeting_id, a.get("source_time"), f"Action Item {n}", warnings) or (None, None)
        item_code = (a.get("id") or "").strip()
        if not item_code:
            same = by_text.get(" ".join(a["text"].split()).lower())
            if same and same not in listed:
                item_code = same
        if item_code in existing:
            listed.add(item_code)
            conn.execute(
                """UPDATE action_items SET text = %s, owner_person_id = %s, owner_unit_id = %s, due_date = %s, updated_at = now(),
                          source_transcript_id = coalesce(%s, source_transcript_id),
                          source_seq = CASE WHEN %s::bigint IS NULL THEN source_seq ELSE %s END
                   WHERE id = %s""",
                (a["text"], owner_person, owner_unit, due, source[0], source[0], source[1], existing[item_code]),
            )
            continue
        if item_code:
            warnings.append(f"Action Item {n}: id '{item_code}'는 이 회의에 없어 새 항목으로 저장")
        new_code = f"{code}-A{next_item_number(conn, meeting_id, code)}"
        conn.execute(
            """INSERT INTO action_items (code, meeting_id, text, owner_person_id, owner_unit_id, due_date, source_transcript_id, source_seq)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (new_code, meeting_id, a["text"], owner_person, owner_unit, due, *source),
        )
        listed.add(new_code)
    if not keep_unlisted:
        gone = [c for c in existing if c not in listed]
        if gone:
            conn.execute("DELETE FROM action_items WHERE meeting_id = %s AND code = ANY(%s)", (meeting_id, gone))


def save_minutes(
    *,
    store: MeetingStore,
    viewer: Viewer,
    title: str,
    date: str,
    summary: str,
    decisions: list[dict[str, Any]] | None = None,
    action_items: list[dict[str, Any]] | None = None,
    attendees: list[dict[str, Any]] | None = None,
    topics: list[str] | None = None,
    body: str | None = None,
    classification: int = 2,
    location: str | None = None,
    meeting_id: str | None = None,
    finalize: bool = False,
    correction_reason: str | None = None,
    transcript_id: str | None = None,
    flag_reviews: list[dict[str, Any]] | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    checkin_code: str | None = None,
) -> dict[str, Any]:
    if not viewer.can_write:
        return _fail("다른 Agent System이 보낸 요청으로는 회의 기록을 바꿀 수 없습니다. 이 시스템의 사용자가 직접 요청해야 합니다.")
    if classification not in (1, 2, 3, 4):
        return _fail("classification은 1(공개) · 2(사내) · 3(대외비) · 4(극비) 중 하나여야 합니다.")
    if classification > viewer.clearance:
        return _fail(f"요청자의 열람 등급({viewer.clearance})보다 높은 등급({classification})으로는 저장할 수 없습니다.")
    try:
        day = parse_date(date)
    except ValueError:
        day = None
    if day is None:
        return _fail("date는 회의 날짜(YYYY-MM-DD)여야 합니다.")
    try:
        start_clock, end_clock = parse_time_of_day(start_time), parse_time_of_day(end_time)
    except ValueError:
        return _fail("start_time · end_time은 한국 시각 HH:MM이어야 합니다 (예: 14:00).")
    if start_clock and end_clock and end_clock <= start_clock:
        return _fail("end_time은 start_time보다 늦어야 합니다.")
    decisions, action_items, attendees = decisions or [], action_items or [], attendees or []
    warnings: list[str] = []
    created: list[str] = []
    transcript = None
    if transcript_id and transcript_id.strip():
        try:
            transcript = fetch_transcript(transcript_id)
        except TranscriptUnavailable as e:
            warnings.append(f"transcript를 연결하지 못함: {e}")

    with store.connect() as conn:
        if meeting_id:
            row = conn.execute(
                f"""SELECT m.id, m.code, m.starts_at, m.ends_at, mi.status AS minutes_status, mi.version
                    FROM meetings m LEFT JOIN minutes mi ON mi.meeting_id = m.id
                    WHERE m.code = %(code)s AND {VISIBLE} FOR UPDATE OF m""",
                {"code": meeting_id.strip(), **viewer.scope()},
            ).fetchone()
            if row is None:
                return _fail(f"회의 '{meeting_id}'를 찾을 수 없거나 열람 권한이 없습니다.")
            mid, code, was = row["id"], row["code"], row["minutes_status"]
            if was == "final" and not (correction_reason or "").strip():
                return _fail("이미 확정된 회의록입니다. 정정하려면 정정 사유(correction_reason)를 함께 보내야 합니다.")
            if was == "final" and not can_confirm(conn, viewer, mid):
                return _fail(confirm_denied())
            # 시각은 알려 준 것만 바꾸고, 날짜가 바뀌면 원래 시각을 그 날짜로 옮긴다
            clock = start_clock or (row["starts_at"].astimezone(KST).time() if row["starts_at"] else DEFAULT_MEETING_TIME)
            starts = datetime.combine(day, clock, KST)
            end_at = end_clock or (row["ends_at"].astimezone(KST).time() if row["ends_at"] else None)
            ends = datetime.combine(day, end_at, KST) if end_at and end_at > clock else None
            if was == "final":
                conn.execute(
                    "INSERT INTO minutes_revisions (meeting_id, version, snapshot, changed_by, reason) VALUES (%s, %s, %s, %s, %s)",
                    (mid, row["version"], Jsonb(minutes_record(conn, mid)), viewer.email, correction_reason.strip()),
                )
            conn.execute(
                """UPDATE meetings SET title = %s, starts_at = %s, ends_at = %s, location = coalesce(%s, location), topics = %s,
                                       classification = %s, status = 'held', updated_at = now() WHERE id = %s""",
                (title, starts, ends, location, topics or [], classification, mid),
            )
        else:
            was = None
            code = new_meeting_code(conn, day)
            mid = conn.execute(
                """INSERT INTO meetings (code, title, status, starts_at, ends_at, location, topics, classification, created_by)
                   VALUES (%s, %s, 'held', %s, %s, %s, %s, %s, %s) RETURNING id""",
                (code, title, datetime.combine(day, start_clock or DEFAULT_MEETING_TIME, KST),
                 datetime.combine(day, end_clock, KST) if end_clock else None, location, topics or [], classification, viewer.email),
            ).fetchone()["id"]

        # Judged on the participants as they were: a call cannot name its requester secretary and finalize at once.
        confirmer = can_confirm(conn, viewer, mid)
        if attendees:
            _write_participants(conn, mid, attendees, created)
        # After the participants, so speaker labels find the people just named; before the decisions that cite it.
        attached = attach_transcript(conn, mid, transcript, viewer, warnings) if transcript else None
        _write_decisions(conn, mid, decisions, viewer, created, warnings)
        _write_action_items(conn, mid, code, action_items, keep_unlisted=was == "final", created=created, warnings=warnings)
        # Last, so a name the model wrote for someone who checked in is merged into that account.
        link_session(conn, mid, viewer, checkin_code, from_recording=transcript is not None, warnings=warnings)
        apply_checkins(conn, mid)

        if was is None:
            conn.execute(
                "INSERT INTO minutes (meeting_id, status, summary, body, drafted_by) VALUES (%s, 'draft', %s, %s, %s)",
                (mid, summary, body, viewer.email),
            )
        else:
            conn.execute(
                """UPDATE minutes SET summary = %s, body = coalesce(%s, body), updated_at = now(),
                          version = version + CASE WHEN status = 'final' THEN 1 ELSE 0 END WHERE meeting_id = %s""",
                (summary, body, mid),
            )

        scan_minutes(conn, mid, code)
        if attached:
            scan_transcript(conn, mid, attached)
        if flag_reviews:
            if confirmer:
                apply_reviews(conn, mid, flag_reviews, viewer, warnings)
            else:
                warnings.append("Compliance 검토 결과는 반영하지 않았습니다: " + confirm_denied())

        missing = _missing_for_final(conn, mid)
        for line in missing:
            warnings.append(line)
        open_flags = open_flag_count(conn, mid)
        finalized = False
        if finalize and was != "final":
            if not confirmer:
                warnings.append("확정하지 않고 초안으로 저장했습니다. " + confirm_denied())
            elif missing:
                warnings.append("담당자 · 기한이 빠진 Action Item이 있어 확정하지 않고 초안으로 저장했습니다.")
            elif open_flags:
                warnings.append(f"Compliance 검토가 끝나지 않은 항목 {open_flags}건이 있어 확정하지 않고 초안으로 저장했습니다 "
                                "(flag_reviews로 cleared 또는 acknowledged를 보내면 확정할 수 있습니다).")
            else:
                conn.execute(
                    "UPDATE minutes SET status = 'final', finalized_by = %s, finalized_at = now() WHERE meeting_id = %s",
                    (viewer.email, mid),
                )
                finalized = True
        record = minutes_record(conn, mid)
        mails = []
        if finalized or was == "final":
            mails = queue_minutes_mail(conn, mid, record, viewer, corrected=was == "final")
            warnings.extend(unit_owned(conn, mid))
        flags = flags_of(conn, mid)
        attendance = attendance_view(conn, mid)
        carried = recall(conn, mid, viewer)
        transcripts = [r["source_ref"] for r in conn.execute(
            "SELECT source_ref FROM transcripts WHERE meeting_id = %s AND source_ref IS NOT NULL ORDER BY id", (mid,))]

    return {
        "saved": True,
        "meeting_id": code,
        "status": record["status"],
        "version": record["version"],
        "finalized": finalized,
        "corrected": was == "final",
        "warnings": warnings,
        "compliance_flags": flags,
        "transcripts": transcripts,
        "mail_queued": mails,
        "new_directory_entries": created,
        "attendance": attendance,
        "recall": carried,
        "record": record,
    }
