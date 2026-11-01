"""Meeting transcripts — part of the save_meeting_minutes workflow, and their retention.

A recording is transcribed by the speech service, which keeps the speaker-labelled result for a
short while and puts its id on the first line of the text the user sees ("[transcript_id: TR-…]").
The model passes only that id; the transcript itself is fetched here over the internal network
(SPEECH_SERVICE_URL, Bearer SPEECH_API_KEY) and copied into this system's schema, so tens of
thousands of characters never go through a Tool argument. A copied transcript expires after
MEETINGS_TRANSCRIPT_RETENTION_DAYS (default 365) and is deleted by the background job; decisions
and Action Items keep their text, only their link to the source statement becomes empty.
Not agent-visible; no LangChain dependency.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import psycopg

from .meeting_store import Viewer, find_person, unnamed_speaker

TRANSCRIPT_ID = re.compile(r"^TR-\d{8}-[0-9A-F]{6}$")
_CLOCK = re.compile(r"^\s*\[?\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s*\]?\s*$")


class TranscriptUnavailable(RuntimeError):
    """The speech service is not configured, unreachable, or does not have that transcript (any more)."""


def retention_days() -> int:
    try:
        return max(1, int(os.environ.get("MEETINGS_TRANSCRIPT_RETENTION_DAYS", "365")))
    except ValueError:
        return 365


def fetch_transcript(ref: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """The speech service's transcript `ref`: {id, source_name, duration, engine, turns[{start, end, speaker, score, text}]}."""
    ref = ref.strip().upper()
    if not TRANSCRIPT_ID.match(ref):
        raise TranscriptUnavailable(f"transcript id '{ref}'의 형식이 아닙니다 (예: TR-20261028-1A2B3C).")
    base = os.environ.get("SPEECH_SERVICE_URL", "").rstrip("/")
    if not base:
        raise TranscriptUnavailable("음성 변환 서비스(SPEECH_SERVICE_URL)가 설정되지 않았습니다.")
    headers = {"Authorization": f"Bearer {os.environ.get('SPEECH_API_KEY', '')}"}
    try:
        http = client or httpx.Client(timeout=10.0)
        try:
            res = http.get(f"{base}/internal/transcripts/{ref}", headers=headers)
        finally:
            if client is None:
                http.close()
    except httpx.HTTPError as e:
        raise TranscriptUnavailable(f"음성 변환 서비스에 연결할 수 없습니다: {type(e).__name__}") from e
    if res.status_code == 404:
        raise TranscriptUnavailable(f"transcript '{ref}'가 없거나 보관 기간이 지나 삭제되었습니다.")
    if res.status_code != 200:
        raise TranscriptUnavailable(f"음성 변환 서비스가 transcript를 주지 않았습니다 (HTTP {res.status_code}).")
    return res.json()


def attach_transcript(conn: psycopg.Connection, meeting_id: int, data: dict[str, Any], viewer: Viewer,
                      warnings: list[str]) -> int | None:
    """Copies a fetched transcript into the meeting; returns its row id (None if it belongs to another meeting)."""
    ref = str(data["id"]).upper()
    existing = conn.execute("SELECT id, meeting_id FROM transcripts WHERE source_ref = %s", (ref,)).fetchone()
    if existing:
        if existing["meeting_id"] != meeting_id:
            warnings.append(f"transcript {ref}는 이미 다른 회의에 연결되어 있어 이 회의에는 붙이지 않았습니다.")
            return None
        return existing["id"]
    expires = datetime.now(timezone.utc) + timedelta(days=retention_days())
    tid = conn.execute(
        """INSERT INTO transcripts (meeting_id, source_ref, source_name, duration_s, stt_engine, expires_at, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (meeting_id, ref, data.get("source_name"), data.get("duration"), data.get("engine"), expires, viewer.email),
    ).fetchone()["id"]
    for seq, t in enumerate(data.get("turns") or [], 1):
        speaker = str(t.get("speaker") or "미확인 화자")
        conn.execute(
            """INSERT INTO transcript_segments (transcript_id, seq, start_s, end_s, speaker_label, speaker_score, person_id, text)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (tid, seq, float(t["start"]), float(t["end"]), speaker, t.get("score"),
             None if unnamed_speaker(speaker) else find_person(conn, speaker), str(t.get("text") or "")),
        )
    return tid


def parse_clock(value: str | None) -> float | None:
    """'03:15' / '[01:02:03]' → seconds; None when it is not a transcript time stamp."""
    m = _CLOCK.match(value or "")
    if not m:
        return None
    hours, minutes, seconds = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    return float(hours * 3600 + minutes * 60 + seconds)


def segment_at(conn: psycopg.Connection, meeting_id: int, stamp: str | None) -> tuple[int, int] | None:
    """(transcript_id, seq) of the meeting's statement shown at `stamp` (the [mm:ss] the model saw)."""
    seconds = parse_clock(stamp)
    if seconds is None:
        return None
    row = conn.execute(
        """SELECT s.transcript_id, s.seq FROM transcript_segments s JOIN transcripts t ON t.id = s.transcript_id
           WHERE t.meeting_id = %s AND s.start_s >= %s - 1 AND s.start_s < %s + 1
           ORDER BY abs(s.start_s - %s), t.id DESC LIMIT 1""",
        (meeting_id, seconds, seconds, seconds),
    ).fetchone()
    return (row["transcript_id"], row["seq"]) if row else None


def purge_expired(conn: psycopg.Connection) -> int:
    """Deletes transcripts past their retention date (segments go with them; links become NULL)."""
    return conn.execute("DELETE FROM transcripts WHERE expires_at IS NOT NULL AND expires_at < now()").rowcount
