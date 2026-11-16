"""Decision watch — a decision's premises after the meeting (behind the run_decision_review Tool and the watch job).

A decision is kept with the premises it rests on and the conditions it must meet (decision_premises, written by
save_meeting_minutes). The meeting ends; the decision goes on, and what it assumed can stop being true:

  scan_articles(conn, articles, ask)  the watch job: each article dated on or after a watched decision's meeting day
                                      that mentions a premise's watch words is held against that premise once; the
                                      model says whether it breaks the premise (broken), shakes it (at_risk) or not
                                      (none) — every pair is kept in premise_signals so it is never asked again.
                                      A model that does not answer leaves the words match as at_risk (judged
                                      'keywords') for a person to check. A new broken / at_risk signal sets the
                                      premise's status and mails the chair, the secretary and who stated the premise.
  review_decisions(...)               the Tool: the decisions in force that a question or a new plan touches, with
                                      their premises, signals and cited statements; the plan's amounts against the
                                      decided ones (conflicts); a re-review meeting (agenda, attendees, the meeting to
                                      carry over from) when a premise broke or the plan goes past a decision —
                                      or the re-review already on the calendar (scheduled_review).
  open_signals(...) / set_signal(...) the desk: signals still to look at, and a person's 'reviewed' / 'dismissed'.

Watched: premises of decisions, resolutions and criteria in final minutes that no later decision superseded.
The model is passed in as ask(system, user) -> text (like transcript_correction); no LangChain dependency.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import date
from typing import Any

import psycopg

from .meeting_context_retrieval import visible_meetings
from .meeting_store import (
    PERSON_LABEL, VISIBLE, MeetingStore, Viewer, can_confirm, confirm_denied, decision_ref, kst_date, kst_minute, time_label, today_kst,
)
from .outbox import queue

Ask = Callable[[str, str], str]

WATCHED_KINDS = ("decision", "resolution", "criterion")
EFFECTS = ("broken", "at_risk", "none")
SHAKEN = ("at_risk", "broken")
SEVERITY = {"holding": 0, "met": 0, "at_risk": 1, "broken": 2}
SIGNAL_ACTIONS = ("list", "dismiss", "reviewed")
CALL_TIMEOUT_SECONDS = 60
ALERT_ROLES = ("chair", "secretary")  # also: who made the decision, who stated the premise
EFFECT_LABEL = {"broken": "전제가 깨짐", "at_risk": "전제가 흔들림"}
_STOPWORDS = {"현재", "수준", "유지", "내년", "올해", "까지", "이상", "이하", "달성", "전제", "조건", "경우", "정도"}
_TOKEN = re.compile(r"[0-9A-Za-z가-힣]+")


def signal_ref(signal_id: int) -> str:
    return f"SIG-{signal_id}"


# ---------------------------------------------------------------------------
# Articles against premises
# ---------------------------------------------------------------------------


def _article_text(a: dict[str, Any]) -> str:
    return " ".join([a.get("headline", ""), a.get("summary", ""), *a.get("key_facts", []), *a.get("companies", [])]).lower()


def mentions(premise: dict[str, Any], article: dict[str, Any]) -> list[str]:
    """The premise's watch words the article uses. Without watch words: two of the statement's words (a trailing
    particle dropped: 단가가 → 단가) must appear."""
    text = _article_text(article)
    words = [w for w in premise.get("watch") or [] if w.strip()]
    if words:
        return [w for w in words if w.lower() in text]
    found = []
    for raw in _TOKEN.findall(premise["statement"].lower()):
        stem = raw[:-1] if len(raw) > 2 and re.match(r"[가-힣]", raw[-1]) else raw
        if len(stem) >= 2 and stem not in _STOPWORDS and stem in text and stem not in found:
            found.append(stem)
    return found if len(found) >= 2 else []


JUDGE_SYSTEM = ("너는 회의에서 내린 결정의 전제를 지켜보는 담당자다. 기사 하나가 결정의 전제 · 조건을 깨는지 판단한다. "
                "기사에 있는 사실만 근거로 쓰고, JSON 객체 하나로만 답한다.")


def judge_prompt(premise: dict[str, Any], article: dict[str, Any]) -> str:
    what = "조건" if premise["kind"] == "condition" else "전제"
    return (f"결정 ({decision_ref(premise['code'], premise['seq'])}, {premise['title']}): {premise['decision']}\n"
            f"{what}: {premise['statement']}\n"
            f"재검토 기준: {premise.get('review_when') or '(회의에서 밝히지 않음)'}\n\n"
            f"기사 ({article.get('date')}, {article.get('source')}): {article.get('headline')}\n"
            f"요약: {article.get('summary', '')}\n"
            f"핵심 사실: {'; '.join(article.get('key_facts') or [])}\n\n"
            '답 형식: {"effect": "broken" | "at_risk" | "none", "reason": "기사의 사실과 숫자로 한 문장"}\n'
            "- broken: 기사의 사실이 재검토 기준에 이르렀거나, 전제가 더는 맞지 않는다\n"
            "- at_risk: 전제를 흔들 수 있는 신호지만 재검토 기준에는 이르지 않았거나 확실하지 않다\n"
            "- none: 이 전제와 관계없거나, 전제와 맞는다")


class JudgeError(Exception):
    pass


def parse_judgement(text: str) -> tuple[str, str]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise JudgeError("답에 JSON이 없습니다")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise JudgeError(f"JSON을 읽을 수 없습니다 ({e.msg})") from e
    effect = str(data.get("effect", "")).strip().lower() if isinstance(data, dict) else ""
    if effect not in EFFECTS:
        raise JudgeError(f"effect '{effect}'는 {', '.join(EFFECTS)} 중 하나가 아닙니다")
    return effect, str(data.get("reason") or "").strip()


def judge(premise: dict[str, Any], article: dict[str, Any], ask: Ask | None, timeout: float = CALL_TIMEOUT_SECONDS) -> tuple[str, str, str]:
    """(effect, reason, judged). Without an answer the words match stands as at_risk, for a person to check."""
    if ask is not None:
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            effect, reason = parse_judgement(pool.submit(ask, JUDGE_SYSTEM, judge_prompt(premise, article)).result(timeout=timeout))
            return effect, reason, "model"
        except FutureTimeout:
            failed = f"{timeout:.0f}초 안에 답이 없음"
        except JudgeError as e:
            failed = str(e)
        except Exception as e:  # the model is down: the words match still reaches a person
            failed = type(e).__name__
        finally:
            pool.shutdown(wait=False)
    else:
        failed = "모델 없음"
    words = ", ".join(mentions(premise, article))
    return "at_risk", f"모델 판단 실패({failed}) — 전제의 낱말({words})이 기사에 있어 표시함, 확인 필요", "keywords"


def watched(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Premises of decisions in force: final minutes, not superseded, not met."""
    return conn.execute(
        f"""SELECT dp.id, dp.kind, dp.statement, dp.review_when, dp.watch, dp.status, dp.speaker_id,
                   coalesce({PERSON_LABEL}, dp.speaker_label) AS speaker,
                   d.id AS decision_id, d.seq, d.statement AS decision, d.speaker_id AS decided_by,
                   m.id AS meeting_id, m.code, m.title, m.starts_at
            FROM decision_premises dp JOIN decisions d ON d.id = dp.decision_id JOIN meetings m ON m.id = d.meeting_id
            JOIN minutes mi ON mi.meeting_id = m.id LEFT JOIN people p ON p.id = dp.speaker_id
            WHERE mi.status = 'final' AND d.kind = ANY(%s) AND dp.status <> 'met'
              AND NOT EXISTS (SELECT 1 FROM decisions s WHERE s.supersedes_id = d.id)
            ORDER BY m.starts_at, d.seq, dp.seq""",
        (list(WATCHED_KINDS),),
    ).fetchall()


def scan_articles(conn: psycopg.Connection, articles: list[dict[str, Any]], ask: Ask | None, *,
                  timeout: float = CALL_TIMEOUT_SECONDS) -> dict[str, int]:
    """One pass of the watch job: {checked, shaken, mailed}."""
    out = {"checked": 0, "shaken": 0, "mailed": 0}
    for premise in watched(conn):
        day = kst_date(premise["starts_at"])
        seen = {r["source_ref"] for r in conn.execute("SELECT source_ref FROM premise_signals WHERE premise_id = %s", (premise["id"],))}
        for article in articles:
            if article["id"] in seen or str(article.get("date", "")) < day or not mentions(premise, article):
                continue
            effect, reason, judged = judge(premise, article, ask, timeout)
            row = conn.execute(
                """INSERT INTO premise_signals (premise_id, source_kind, source_ref, source_name, source_date, headline, summary,
                                                effect, reason, judged)
                   VALUES (%s, 'news', %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (premise_id, source_ref) DO NOTHING RETURNING id""",
                (premise["id"], article["id"], article.get("source"), article.get("date"), article["headline"],
                 article.get("summary"), effect, reason or None, judged),
            ).fetchone()
            out["checked"] += 1
            if row is None or effect not in SHAKEN:
                continue
            out["shaken"] += 1
            if SEVERITY[effect] > SEVERITY[premise["status"]]:
                conn.execute("UPDATE decision_premises SET status = %s WHERE id = %s", (effect, premise["id"]))
                premise["status"] = effect
            out["mailed"] += _alert(conn, premise, article, effect, reason, judged)
    return out


def _alert(conn: psycopg.Connection, premise: dict[str, Any], article: dict[str, Any], effect: str,
           reason: str, judged: str) -> int:
    """Mail about a new signal to the meeting's chair and secretary (everyone who attended when nobody holds those
    roles), who made the decision and who stated the premise — once per decision and article, however many of
    the decision's premises the article shakes."""
    people = [r["person_id"] for r in conn.execute(
        "SELECT person_id FROM meeting_participants WHERE meeting_id = %s AND role = ANY(%s) ORDER BY role",
        (premise["meeting_id"], list(ALERT_ROLES)))]
    if not people:
        people = [r["person_id"] for r in conn.execute(
            "SELECT person_id FROM meeting_participants WHERE meeting_id = %s AND attended IS NOT FALSE ORDER BY person_id",
            (premise["meeting_id"],))]
    for extra in (premise["decided_by"], premise["speaker_id"]):
        if extra and extra not in people:
            people.append(extra)
    ref = decision_ref(premise["code"], premise["seq"])
    what = "조건" if premise["kind"] == "condition" else "전제"
    subject = f"[{what} 변화] {premise['title']} — {ref}"
    said = f" (발언: {premise['speaker']})" if premise["speaker"] else ""
    body = (f"{kst_date(premise['starts_at'])} {premise['title']}에서 내린 결정의 {what}이 흔들렸습니다.\n\n"
            f"결정 ({ref}): {premise['decision']}\n"
            f"{what}: {premise['statement']}{said}\n"
            + (f"재검토 기준: {premise['review_when']}\n" if premise["review_when"] else "")
            + f"\n새 정보 ({article.get('source')}, {article.get('date')}): {article['headline']}\n{article.get('summary', '')}\n\n"
            f"Agent 판단: {EFFECT_LABEL[effect]} — {reason}\n"
            + ("(모델이 판단하지 못해 낱말이 겹치는 것만 확인했습니다. 내용을 확인해 주세요.)\n" if judged == "keywords" else "")
            + f"\n재검토가 필요하면 회의지원 Agent에 \"{ref} 재검토 회의를 제안해 줘\"라고 요청하세요. "
            "문제없으면 회의지원 채팅 첫 화면의 '전제가 흔들린 결정'에서 '문제없음'으로 닫을 수 있습니다.")
    sent = 0
    for person in people:
        if queue(conn, kind="alert", meeting_id=premise["meeting_id"], person_id=person, subject=subject, body=body,
                 created_by="agent", dedupe_key=f"alert:{premise['decision_id']}:{article['id']}:{person}") is not None:
            sent += 1
    return sent


def watch_news(store: MeetingStore, articles: list[dict[str, Any]], ask: Ask | None) -> dict[str, int]:
    """The job's pass over the store (empty when another process is scanning it)."""
    with store.connect() as conn:
        if not conn.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS ok", (f"{store.schema}:watch",)).fetchone()["ok"]:
            return {}
        return scan_articles(conn, articles, ask)


# ---------------------------------------------------------------------------
# A question or a new plan against the decisions in force
# ---------------------------------------------------------------------------

# 1조 1천억 · 9천억 · 9,000억 · 2.5조 · 900억 → 억 원
_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*조(?:\s*(\d+(?:,\d{3})*)\s*(천|백)?\s*억)?|(\d+(?:,\d{3})*(?:\.\d+)?)\s*(천|백)?\s*억")
_UNIT = {"천": 1000, "백": 100, None: 1, "": 1}


def amounts(text: str) -> list[float]:
    """Money amounts in 억 원 (Korean units: 조 = 10,000억)."""
    out = []
    for m in _AMOUNT.finditer(text or ""):
        if m.group(1):
            rest = float(m.group(2).replace(",", "")) * _UNIT[m.group(3)] if m.group(2) else 0
            out.append(float(m.group(1)) * 10000 + rest)
        else:
            out.append(float(m.group(4).replace(",", "")) * _UNIT[m.group(5)])
    return out


def won(eok: float) -> str:
    """억 원 → '1조 1,000억 원' / '9,000억 원'."""
    jo, rest = divmod(round(eok), 10000)
    if jo and rest:
        return f"{jo}조 {rest:,}억 원"
    return f"{jo}조 원" if jo else f"{rest:,}억 원"


def _attendees(conn: psycopg.Connection, meeting_code: str) -> list[str]:
    """The decision meeting's people as run_meeting_schedule attendee labels ('김민수 (CEO, 의장)')."""
    names = {"chair": "의장", "secretary": "간사"}
    rows = conn.execute(
        """SELECT p.display_name, p.title, mp.role FROM meeting_participants mp JOIN people p ON p.id = mp.person_id
           JOIN meetings m ON m.id = mp.meeting_id WHERE m.code = %s AND mp.attended IS NOT FALSE
           ORDER BY array_position(ARRAY['chair', 'secretary', 'required', 'optional', 'observer'], mp.role), p.display_name""",
        (meeting_code,),
    ).fetchall()
    out = []
    for r in rows:
        inside = [x for x in (r["title"], names.get(r["role"])) if x]
        out.append(f"{r['display_name']} ({', '.join(inside)})" if inside else r["display_name"])
    return out


# Words of a question that say nothing about which decision it is about
_ASKING = {"지난", "결정", "결정과", "결정이", "확인", "확인해", "주세요", "회의", "회의에서", "계속", "가도", "지금", "그대로", "맞는지",
           "해도", "있어", "있나요", "세부", "계획", "계획을", "보냈습니다", "보냈어"}
_RUN = re.compile(r"[0-9A-Za-z]+|[가-힣]+")  # 'HBM사업부가' → HBM, 사업부가
MAX_DECISIONS = 3


def _words(text: str) -> set[str]:
    return {w for w in _RUN.findall(text.lower()) if len(w) >= 2 and w not in _ASKING and not w.isdigit()}


def relevance(d: dict[str, Any], meeting: dict[str, Any], words: set[str], with_amounts: bool) -> int:
    """How much a decision is about the question: its words in the decision, its premises (and their watch words),
    the meeting's title and topics (a Korean word also counts without its last syllable: 계획을 → 계획);
    a decision resting on premises, or naming an amount when the question has one, comes first."""
    premises = d.get("premises", [])
    text = " ".join([d["statement"], meeting["title"], *meeting["topics"], *(p["statement"] for p in premises),
                     *(w for p in premises for w in p.get("watch", []))]).lower()
    hits = sum(1 for w in words if w in text or (len(w) > 2 and not w.isascii() and w[:-1] in text))
    score = hits + (2 if premises else 0) + (1 if with_amounts and amounts(d["statement"]) else 0)
    return score - (2 if d.get("superseded_by") else 0)


def review_decisions(query: str, topics: list[str] | None = None, *, store: MeetingStore, viewer: Viewer,
                     decision: str | None = None, today: date | None = None) -> dict[str, Any]:
    """The decisions in force that `query` is about (scored one by one, not by meeting: a meeting's other business
    must not hide the decision asked about), or the one `decision` names."""
    today = today or today_kst()
    ref = (decision or "").strip()
    meeting_id = ref.rsplit("-D", 1)[0] if "-D" in ref else None
    proposed = amounts(query)
    words = _words(f"{query} {' '.join(topics or [])}")
    scored = []
    for m in visible_meetings(store, viewer, meeting_id=meeting_id, today=today):
        for d in m["decisions"]:
            if d["kind"] in WATCHED_KINDS and (not ref or d["ref"] == ref):
                scored.append((relevance(d, m, words, bool(proposed)), m["date"],
                               {**d, "meeting": {"id": m["id"], "title": m["title"], "date": m["date"], "status": m["status"]}}))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)  # best first, the newest meeting first within a score
    best = scored[0][0] if scored else 0
    decisions = [d for s, _, d in scored if ref or s >= max(2, best - 1)][:MAX_DECISIONS]
    conflicts = []
    for d in decisions:
        decided = amounts(d["statement"])
        if proposed and decided and not d.get("superseded_by") and max(proposed) > max(decided):
            conflicts.append({"decision": d["ref"], "decided": won(max(decided)), "proposed": won(max(proposed)),
                              "over_by": won(max(proposed) - max(decided)),
                              "note": "결정한 금액을 넘는다 — 그 회의체의 재승인이 필요하다"})
    shaken = [(d, p) for d in decisions if not d.get("superseded_by") for p in d.get("premises", []) if p["status"] in SHAKEN]
    out: dict[str, Any] = {"as_of": today.isoformat(), "query": query, "decisions": decisions,
                           "proposed_amounts": [won(a) for a in proposed], "conflicts": conflicts}
    if not decisions:
        out["note"] = "열람 가능한 확정 회의록에서 관련된 결정을 찾지 못했습니다."
    if conflicts or shaken:
        base = (shaken[0][0] if shaken else next(d for d in decisions if d["ref"] == conflicts[0]["decision"]))["meeting"]
        agenda = []
        for d, p in shaken:
            news = next((s for s in p.get("signals", []) if s["effect"] in SHAKEN), None)
            agenda.append(f"{d['ref']} 전제 재검토: {p['statement']}" + (f" — {news['headline']}" if news else ""))
        for c in conflicts:
            agenda.append(f"새 계획 {c['proposed']} 승인 여부 — {c['decision']} 결정 {c['decided']} 초과")
        title = f"{base['title']} 재검토"
        with store.connect() as conn:
            booked = _booked_review(conn, viewer, title, [d["ref"] for d in decisions])
            attendees = _attendees(conn, base["id"])
        if booked:
            out["scheduled_review"] = booked
        else:
            out["re_review"] = {"title": title, "agenda": agenda, "attendees": attendees, "carry_over_from": base["id"]}
    return out


def _booked_review(conn: psycopg.Connection, viewer: Viewer, title: str, refs: list[str]) -> dict[str, Any] | None:
    """A re-review already on the calendar: an upcoming meeting with that title or an agenda item naming one of the
    decisions — so the same re-review is not proposed twice."""
    row = conn.execute(
        f"""SELECT m.code, m.title, m.starts_at, m.ends_at, m.location FROM meetings m
            WHERE {VISIBLE} AND m.status = 'scheduled' AND m.starts_at >= now() - interval '1 hour'
              AND (m.title = %(title)s OR EXISTS (SELECT 1 FROM agenda_items a WHERE a.meeting_id = m.id AND a.title LIKE ANY(%(refs)s)))
            ORDER BY m.starts_at LIMIT 1""",
        {**viewer.scope(), "title": title, "refs": [f"%{r}%" for r in refs]},
    ).fetchone()
    if not row:
        return None
    agenda = [r["title"] for r in conn.execute(
        "SELECT a.title FROM agenda_items a JOIN meetings m ON m.id = a.meeting_id WHERE m.code = %s ORDER BY a.seq", (row["code"],))]
    return {"id": row["code"], "title": row["title"], "starts_at": kst_minute(row["starts_at"]),
            "time": time_label(row["starts_at"], row["ends_at"]), "location": row["location"], "agenda": agenda}


# ---------------------------------------------------------------------------
# Desk: signals to look at
# ---------------------------------------------------------------------------


def open_signals(conn: psycopg.Connection, viewer: Viewer, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""SELECT s.id, s.effect, s.reason, s.judged, s.headline, s.source_name, s.source_date, s.created_at,
                   dp.kind, dp.statement AS premise, dp.review_when, dp.status AS premise_status,
                   d.seq, d.statement AS decision, m.code, m.title, m.starts_at
            FROM premise_signals s JOIN decision_premises dp ON dp.id = s.premise_id JOIN decisions d ON d.id = dp.decision_id
            JOIN meetings m ON m.id = d.meeting_id
            WHERE {VISIBLE} AND s.status = 'open' AND s.effect <> 'none'
              AND NOT EXISTS (SELECT 1 FROM decisions n WHERE n.supersedes_id = d.id)
            ORDER BY (s.effect = 'broken') DESC, s.created_at DESC LIMIT %(limit)s""",
        {**viewer.scope(), "limit": limit},
    ).fetchall()
    return [{"id": signal_ref(r["id"]), "effect": r["effect"], "reason": r["reason"], "judged": r["judged"],
             "news": {"headline": r["headline"], "source": r["source_name"], "date": r["source_date"].isoformat() if r["source_date"] else None},
             "premise": {"kind": r["kind"], "statement": r["premise"], "review_when": r["review_when"], "status": r["premise_status"]},
             "decision": {"ref": decision_ref(r["code"], r["seq"]), "statement": r["decision"]},
             "meeting": {"id": r["code"], "title": r["title"], "date": kst_date(r["starts_at"])}} for r in rows]


def set_signal(*, store: MeetingStore, viewer: Viewer, action: str = "list", signal_id: str | None = None,
               note: str | None = None) -> dict[str, Any]:
    """list · dismiss(문제없음: 전제를 유지로 되돌림) · reviewed(재검토로 넘김). 바꾸는 권한은 그 회의록 확정 권한과 같다."""
    if action not in SIGNAL_ACTIONS:
        return {"ok": False, "error": f"action은 {', '.join(SIGNAL_ACTIONS)} 중 하나여야 합니다."}
    with store.connect() as conn:
        if action == "list":
            return {"signals": open_signals(conn, viewer)}
        if not viewer.can_write:
            return {"ok": False, "error": "다른 Agent System이 보낸 요청으로는 바꿀 수 없습니다."}
        m = re.fullmatch(r"\s*SIG-(\d+)\s*", signal_id or "")
        row = m and conn.execute(
            f"""SELECT s.id, s.premise_id, m.id AS meeting_id FROM premise_signals s JOIN decision_premises dp ON dp.id = s.premise_id
                JOIN decisions d ON d.id = dp.decision_id JOIN meetings m ON m.id = d.meeting_id
                WHERE s.id = %(id)s AND {VISIBLE}""",
            {**viewer.scope(), "id": int(m.group(1))},
        ).fetchone()
        if not row:
            return {"ok": False, "error": "해당하는 신호가 없거나 열람 권한이 없습니다."}
        if not can_confirm(conn, viewer, row["meeting_id"]):
            return {"ok": False, "error": confirm_denied()}
        conn.execute("UPDATE premise_signals SET status = %s, reviewed_by = %s, review_note = %s WHERE id = %s",
                     ("dismissed" if action == "dismiss" else "reviewed", viewer.email, note, row["id"]))
        if action == "dismiss":  # no other open signal against it: the premise holds again
            left = conn.execute(
                "SELECT max(CASE effect WHEN 'broken' THEN 2 ELSE 1 END) AS worst FROM premise_signals "
                "WHERE premise_id = %s AND status = 'open' AND effect <> 'none'", (row["premise_id"],)).fetchone()["worst"]
            conn.execute("UPDATE decision_premises SET status = %s WHERE id = %s AND status <> 'met'",
                         ({None: "holding", 1: "at_risk", 2: "broken"}[left], row["premise_id"]))
        return {"ok": True, "signals": open_signals(conn, viewer)}
