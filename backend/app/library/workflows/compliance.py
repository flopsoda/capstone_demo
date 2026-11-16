"""Compliance review of minutes and transcripts — part of the save_meeting_minutes workflow.

Deterministic rules flag statements that may need legal attention before minutes are final
(insider information, collusion, bribery, accounting fraud, hiding records, unfair labour
practice, exposed personal data). A flag is a prompt for the secretary, not a verdict: the
reviewer marks it cleared (no issue) or acknowledged (issue handled, with a note), and minutes
with open flags are not finalized. Rules err toward flagging; a rule that misses is worse
than one the reviewer clears. Not agent-visible; no LangChain dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import psycopg

from .meeting_store import Viewer

EXCERPT_CONTEXT = 30  # characters kept on each side of a match
REVIEW_STATUSES = ("cleared", "acknowledged")


@dataclass(frozen=True)
class Rule:
    category: str
    name: str  # shown to the reviewer, with the statute it points to
    pattern: re.Pattern[str]


def _rule(category: str, name: str, pattern: str) -> Rule:
    return Rule(category, name, re.compile(pattern))


_GAP = r"[^.。!?\n]{0,14}"  # a few words within one sentence

RULES: tuple[Rule, ...] = (
    _rule("insider_trading", "미공개 중요정보 이용 (자본시장법 제174조)",
          rf"(미공개|공시\s*전|발표\s*전|공개\s*전){_GAP}(주식|지분|주가|매수|매도|사\s*두|팔아)"
          rf"|주식을?\s*(미리|먼저)\s*(사|매수)|내부\s*정보로{_GAP}(투자|매수|사)"),
    _rule("collusion", "부당한 공동행위 · 담합 (공정거래법 제40조)",
          rf"(가격|단가|입찰|낙찰|물량|견적|인상\s*폭){_GAP}(맞추|맞춰|합의|담합|짜고|짜서|나눠|나누|조율)"
          rf"|경쟁사(와|랑|하고|들과){_GAP}(가격|단가|물량|입찰|시장)|들러리"),
    _rule("bribery", "부정청탁 · 금품 제공 (청탁금지법, 형법 제357조)",
          rf"리베이트|뒷돈|떡값|비자금|상품권{_GAP}(드리|전달|챙겨|돌리)"
          rf"|(공무원|심사위원|감독관|평가위원){_GAP}(접대|선물|용돈|골프|챙겨)"),
    _rule("accounting_fraud", "회계 부정 (외부감사법 제39조)",
          rf"분식\s*회계|(매출|실적|손실|비용|부채|충당금){_GAP}(부풀|숨기|숨겨|누락시|조작|빼고\s*보고|뒤로\s*미루)"
          rf"|장부{_GAP}(고치|조작|맞춰)"),
    _rule("record_destruction", "기록 은폐 · 증거 인멸",
          rf"(자료|메일|기록|파일|문서|증거){_GAP}(삭제|파기|지우|지워|없애|폐기)"
          rf"|(회의록|기록)에{_GAP}(남기지|적지|쓰지|빼)|오프\s*더\s*레코드|기록하지\s*(말|마)"),
    _rule("unfair_labor", "부당노동행위 (노동조합법 제81조)",
          rf"노조{_GAP}(가입|활동|결성){_GAP}(불이익|해고|막|배제|못\s*하게)|노조\s*(와해|파괴)"),
    _rule("personal_data", "개인정보 노출 (개인정보보호법 제29조)",
          r"(?<!\d)\d{6}\s*-\s*[1-4]\d{6}(?!\d)|(?<!\d)01[016789]-?\d{3,4}-?\d{4}(?!\d)|주민\s*(등록)?\s*번호"),
)


def scan(text: str | None) -> list[tuple[Rule, str]]:
    """(rule, excerpt) for each rule the text matches — one per rule, at its first match."""
    if not text:
        return []
    found = []
    for rule in RULES:
        m = rule.pattern.search(text)
        if m:
            start, end = max(0, m.start() - EXCERPT_CONTEXT), min(len(text), m.end() + EXCERPT_CONTEXT)
            excerpt = ("…" if start else "") + " ".join(text[start:end].split()) + ("…" if end < len(text) else "")
            found.append((rule, excerpt))
    return found


def flag_ref(flag_id: int) -> str:
    """How a flag is named to the model and the reviewer: CF-12."""
    return f"CF-{flag_id}"


def _insert(conn: psycopg.Connection, meeting_id: int, source: str, location: str, rule: Rule, excerpt: str,
            transcript_id: int | None = None, seq: int | None = None) -> None:
    conn.execute(
        """INSERT INTO compliance_flags (meeting_id, source, location, transcript_id, seq, category, rule, excerpt)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (meeting_id, location, category) DO UPDATE SET excerpt = EXCLUDED.excerpt
           WHERE compliance_flags.status = 'open'""",
        (meeting_id, source, location, transcript_id, seq, rule.category, rule.name, excerpt),
    )


def scan_minutes(conn: psycopg.Connection, meeting_id: int, code: str) -> None:
    """Re-scans the minutes text (summary, body, decisions). Open flags on passages that no longer
    match are dropped (the text was fixed); reviewed flags are kept as the record of the review."""
    minutes = conn.execute("SELECT summary, body FROM minutes WHERE meeting_id = %s", (meeting_id,)).fetchone()
    parts: list[tuple[str, str | None]] = [("요약", minutes["summary"]), ("본문", minutes["body"])]
    for d in conn.execute("SELECT seq, statement, rationale FROM decisions WHERE meeting_id = %s ORDER BY seq", (meeting_id,)):
        parts.append((f"결정 {code}-D{d['seq']}", " ".join(filter(None, [d["statement"], d["rationale"]]))))
    current: set[tuple[str, str]] = set()
    for location, text in parts:
        for rule, excerpt in scan(text):
            current.add((location, rule.category))
            _insert(conn, meeting_id, "minutes", location, rule, excerpt)
    for row in conn.execute(
        "SELECT id, location, category FROM compliance_flags WHERE meeting_id = %s AND source = 'minutes' AND status = 'open'",
        (meeting_id,),
    ).fetchall():
        if (row["location"], row["category"]) not in current:
            conn.execute("DELETE FROM compliance_flags WHERE id = %s", (row["id"],))


def scan_transcript(conn: psycopg.Connection, meeting_id: int, transcript_id: int) -> None:
    for s in conn.execute(
        "SELECT seq, start_s, speaker_label, text FROM transcript_segments WHERE transcript_id = %s ORDER BY seq",
        (transcript_id,),
    ).fetchall():
        for rule, excerpt in scan(s["text"]):
            location = f"transcript [{clock(s['start_s'])}] {s['speaker_label']} #{transcript_id}.{s['seq']}"
            _insert(conn, meeting_id, "transcript", location, rule, excerpt, transcript_id, s["seq"])


def clock(seconds: float) -> str:
    """The [mm:ss] / [hh:mm:ss] stamp speech-service puts on each transcript line."""
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def apply_reviews(conn: psycopg.Connection, meeting_id: int, reviews: list[dict[str, Any]], viewer: Viewer,
                  warnings: list[str]) -> None:
    """[{id: "CF-12", status: cleared|acknowledged, note}] → the flags of this meeting. The caller checks permission."""
    for r in reviews:
        m = re.fullmatch(r"\s*(?:CF-)?(\d+)\s*", str(r.get("id") or ""))
        status = r.get("status")
        if not m or status not in REVIEW_STATUSES:
            warnings.append(f"Compliance 검토 '{r.get('id')}': id(CF-n)와 status(cleared | acknowledged)가 필요합니다.")
            continue
        if status == "acknowledged" and not (r.get("note") or "").strip():
            warnings.append(f"Compliance 검토 {flag_ref(int(m.group(1)))}: acknowledged에는 어떤 조치를 했는지 note가 필요합니다.")
            continue
        updated = conn.execute(
            """UPDATE compliance_flags SET status = %s, review_note = %s, reviewed_by = %s, reviewed_at = now()
               WHERE id = %s AND meeting_id = %s""",
            (status, (r.get("note") or "").strip() or None, viewer.email, int(m.group(1)), meeting_id),
        ).rowcount
        if not updated:
            warnings.append(f"Compliance 검토 {flag_ref(int(m.group(1)))}: 이 회의의 검토 항목이 아닙니다.")


def flags_of(conn: psycopg.Connection, meeting_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT id, location, rule, excerpt, status, review_note, reviewed_by FROM compliance_flags
           WHERE meeting_id = %s ORDER BY status <> 'open', id""",
        (meeting_id,),
    ).fetchall()
    out = []
    for r in rows:
        flag = {"id": flag_ref(r["id"]), "location": r["location"], "rule": r["rule"], "excerpt": r["excerpt"], "status": r["status"]}
        if r["status"] != "open":
            flag["review_note"], flag["reviewed_by"] = r["review_note"], r["reviewed_by"]
        out.append(flag)
    return out


def open_flag_count(conn: psycopg.Connection, meeting_id: int) -> int:
    return conn.execute(
        "SELECT count(*) AS n FROM compliance_flags WHERE meeting_id = %s AND status = 'open'", (meeting_id,)
    ).fetchone()["n"]
