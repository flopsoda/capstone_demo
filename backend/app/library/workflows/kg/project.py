"""Sources → raw layer (kg_mentions, kg_claims, record nodes). One source at a time, only when it changed.

A source is one meeting (its final minutes: decisions, premises and their news signals, action items, attendees)
or one news event (its articles). Its content — and the alias index used to spot names in its text — is hashed;
an unchanged hash is skipped, a changed one replaces everything that source said before. So running the same data
again writes nothing, and an edit in a source (a corrected minutes, a finished action item, a new article in an
event) is reflected the next time.

    meetings   structural, no model: the meeting DB already says who decided what, on what premise, who owes what
               (DECIDED_IN · SUPERSEDES · RESOLVES · RESTS_ON · RAISED_IN · FOLLOWS_UP · ASSIGNED_TO · STATED_BY ·
               ATTENDED), and which names its text uses (CONCERNS, by the alias index). A news item that the decision
               watch judged against a premise is an assessed claim (AFFECTS), kept apart from mere co-mentions.
    news       articles of an event (PART_OF), the names they mention (the analysis' entities, the names the seed
               gave, names of the alias index), and the relations the extraction model reads in them (reported).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

from ..meeting_store import KST, decision_ref, kst_date, unnamed_speaker
from . import extract
from .resolve import stable_id

PROJECTOR = "kg-project-v1"
DICT = "dict-v1"

Ask = Callable[[str, str], str]


# ---------------------------------------------------------------------------
# Names in text (the alias index)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Alias:
    text: str     # lower case, as written
    node_id: str
    type: str


def alias_index(conn: psycopg.Connection) -> list[Alias]:
    """Names of organizations and people the graph knows, longest first. Too-short names (one Hangul syllable,
    two Latin letters) are left out: they match inside other words."""
    rows = conn.execute(
        """SELECT DISTINCT lower(a.alias) AS text, a.node_id, n.type FROM kg_aliases a JOIN kg_nodes n ON n.id = a.node_id
           WHERE n.type IN ('organization', 'person')""").fetchall()
    out = [Alias(r["text"].strip(), r["node_id"], r["type"]) for r in rows]
    out = [a for a in out if len(a.text) >= (3 if a.text.isascii() else 2)]
    return sorted(out, key=lambda a: (-len(a.text), a.text, a.node_id))


def fingerprint(index: list[Alias]) -> str:
    return hashlib.sha256("\n".join(f"{a.text}\t{a.node_id}" for a in index).encode()).hexdigest()[:16]


_PATTERNS: dict[str, re.Pattern[str]] = {}


def _pattern(text: str) -> re.Pattern[str]:
    # A name starts at a word start (so 'K그룹' is not found inside 'SK그룹'); a Korean name may be followed by a
    # particle (뉴로엣지의), a Latin one may not continue as a longer word.
    if text not in _PATTERNS:
        tail = r"(?![0-9a-z])" if text[-1:].isascii() else ""
        _PATTERNS[text] = re.compile(r"(?<![0-9a-z가-힣])" + re.escape(text) + tail)
    return _PATTERNS[text]


def link_text(text: str, index: list[Alias]) -> list[tuple[str, Alias]]:
    """(name as written, alias) for every known name in `text`, one per entity; a longer name wins its span."""
    lower, taken, out, seen = (text or "").lower(), [], [], set()
    for a in index:
        for m in _pattern(a.text).finditer(lower):
            span = range(m.start(), m.end())
            if any(set(span) & set(t) for t in taken):
                continue
            taken.append(span)
            if a.node_id not in seen:
                seen.add(a.node_id)
                out.append((text[m.start():m.end()], a))
    return out


# ---------------------------------------------------------------------------
# Writing one source
# ---------------------------------------------------------------------------


@dataclass
class Source:
    kind: str
    ref: str
    meeting_id: int | None = None
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    mentions: dict[str, dict[str, Any]] = field(default_factory=dict)
    claims: dict[str, dict[str, Any]] = field(default_factory=dict)

    def node(self, id_: str, type_: str, label: str, attrs: dict[str, Any], *, meeting: bool = True) -> str:
        self.nodes[id_] = {"id": id_, "type": type_, "label": label[:200], "attrs": attrs,
                           "meeting_id": self.meeting_id if meeting else None}
        return id_

    def mention(self, record: str, surface: str, *, extractor: str, type_guess: str | None = None,
                aliases: list[str] | None = None, context: str = "") -> str:
        id_ = stable_id(self.kind, self.ref, record, extractor, surface)
        self.mentions[id_] = {"id": id_, "record": record, "surface": surface, "aliases": aliases or [], "type_guess": type_guess,
                              "context": context[:500], "extractor": extractor}
        return f"mention:{id_}"

    def claim(self, subject: str, predicate: str, obj: str, *, cls: str = "structural", extractor: str = PROJECTOR,
              qualifiers: dict[str, Any] | None = None, event_ref: str | None = None, stated_at: str | None = None,
              context: str = "", local: str = "", meeting: bool = True) -> None:
        q = qualifiers or {}
        id_ = stable_id(self.kind, self.ref, extractor, subject, predicate, obj, local)
        self.claims[id_] = {"id": id_, "claim_class": cls, "subject": subject, "predicate_raw": predicate, "object": obj,
                            "qualifiers": q, "event_ref": event_ref, "stated_at": stated_at, "context": context[:500],
                            "extractor": extractor, "meeting_id": self.meeting_id if meeting else None}

    def link_names(self, record: str, text: str, index: list[Alias], predicate: str, **claim_args: Any) -> None:
        for surface, a in link_text(text, index):
            self.claim(record, predicate, self.mention(record, surface, extractor=DICT, type_guess=a.type, context=text),
                       extractor=DICT, context=text, **claim_args)


def stored_hash(conn: psycopg.Connection, kind: str, ref: str) -> str | None:
    row = conn.execute("SELECT source_hash FROM kg_sources WHERE source_kind = %s AND source_ref = %s", (kind, ref)).fetchone()
    return row["source_hash"] if row else None


def write_source(conn: psycopg.Connection, src: Source, source_hash: str) -> None:
    """Replaces everything `src` said before (its facts go at the next rebuild) and records its hash."""
    conn.execute("DELETE FROM kg_claims WHERE source_kind = %s AND source_ref = %s", (src.kind, src.ref))
    conn.execute("DELETE FROM kg_mentions WHERE source_kind = %s AND source_ref = %s", (src.kind, src.ref))
    for n in src.nodes.values():
        conn.execute(
            """INSERT INTO kg_nodes (id, kind, type, label, attrs, meeting_id) VALUES (%s, 'record', %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET type = EXCLUDED.type, label = EXCLUDED.label, attrs = EXCLUDED.attrs,
                   meeting_id = EXCLUDED.meeting_id WHERE kg_nodes.kind = 'record'""",
            (n["id"], n["type"], n["label"], Jsonb(n["attrs"]), n["meeting_id"]))
    for m in src.mentions.values():
        conn.execute(
            """INSERT INTO kg_mentions (id, source_kind, source_ref, record, surface, aliases, type_guess, context, extractor, meeting_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING""",
            (m["id"], src.kind, src.ref, m["record"], m["surface"], m["aliases"], m["type_guess"], m["context"], m["extractor"],
             src.meeting_id))
    for c in src.claims.values():
        conn.execute(
            """INSERT INTO kg_claims (id, source_kind, source_ref, claim_class, subject, predicate_raw, object, qualifiers,
                                      event_ref, stated_at, context, extractor, meeting_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING""",
            (c["id"], src.kind, src.ref, c["claim_class"], c["subject"], c["predicate_raw"], c["object"], Jsonb(c["qualifiers"]),
             c["event_ref"], c["stated_at"], c["context"], c["extractor"], c["meeting_id"]))
    conn.execute(
        """INSERT INTO kg_sources (source_kind, source_ref, source_hash) VALUES (%s, %s, %s)
           ON CONFLICT (source_kind, source_ref) DO UPDATE SET source_hash = EXCLUDED.source_hash, synced_at = now()""",
        (src.kind, src.ref, source_hash))


def digest(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Meetings
# ---------------------------------------------------------------------------


def final_meetings(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Meetings with final minutes (drafts never enter the graph)."""
    return conn.execute("""SELECT m.id, m.code FROM meetings m JOIN minutes mi ON mi.meeting_id = m.id
                           WHERE mi.status = 'final' ORDER BY m.starts_at, m.id""").fetchall()


def load_meeting(conn: psycopg.Connection, meeting_id: int) -> dict[str, Any]:
    q = lambda sql_, *args: [dict(r) for r in conn.execute(sql_, args).fetchall()]  # noqa: E731
    meeting = conn.execute(
        """SELECT m.id, m.code, m.title, m.starts_at, m.topics, m.subject, mi.version, mi.summary, mi.updated_at
           FROM meetings m JOIN minutes mi ON mi.meeting_id = m.id WHERE m.id = %s""", (meeting_id,)).fetchone()
    return {
        "meeting": dict(meeting),
        "decisions": q("""SELECT d.id, d.seq, d.kind, d.statement, d.rationale, d.outcome, d.speaker_id, d.speaker_label,
                                 (SELECT m2.code || '-D' || s.seq FROM decisions s JOIN meetings m2 ON m2.id = s.meeting_id WHERE s.id = d.supersedes_id) AS supersedes,
                                 (SELECT m2.code || '-D' || s.seq FROM decisions s JOIN meetings m2 ON m2.id = s.meeting_id WHERE s.resolved_by_id = d.id LIMIT 1) AS resolves
                          FROM decisions d WHERE d.meeting_id = %s ORDER BY d.seq""", meeting_id),
        "premises": q("""SELECT dp.id, d.seq AS decision_seq, dp.seq, dp.kind, dp.statement, dp.review_when, dp.status, dp.speaker_id
                         FROM decision_premises dp JOIN decisions d ON d.id = dp.decision_id WHERE d.meeting_id = %s
                         ORDER BY d.seq, dp.seq""", meeting_id),
        "signals": q("""SELECT s.id, d.seq AS decision_seq, dp.seq AS premise_seq, s.source_ref, s.source_name, s.source_date,
                               s.headline, s.effect, s.reason, s.judged, s.status
                        FROM premise_signals s JOIN decision_premises dp ON dp.id = s.premise_id JOIN decisions d ON d.id = dp.decision_id
                        WHERE d.meeting_id = %s AND s.effect <> 'none' ORDER BY s.id""", meeting_id),
        "items": q("""SELECT a.code, a.text, a.owner_person_id, a.owner_unit_id, a.due_date, a.status, a.completed_at,
                             (SELECT d.seq FROM decisions d WHERE d.id = a.decision_id) AS decision_seq,
                             coalesce(p.display_name || coalesce(' (' || p.title || ')', ''), u.name) AS owner
                      FROM action_items a LEFT JOIN people p ON p.id = a.owner_person_id LEFT JOIN org_units u ON u.id = a.owner_unit_id
                      WHERE a.meeting_id = %s ORDER BY a.id""", meeting_id),
        "participants": q("""SELECT mp.person_id, mp.role, mp.attended FROM meeting_participants mp JOIN people p ON p.id = mp.person_id
                             WHERE mp.meeting_id = %s AND p.speaker_meeting_id IS NULL ORDER BY mp.person_id""", meeting_id),
        # A speaker recorded only by a title ("CFO") is the person with that title, when exactly one has it.
        "titles": {r["title"].lower(): r["id"] for r in conn.execute(
            """SELECT min(id) AS id, title FROM people WHERE title IS NOT NULL AND speaker_meeting_id IS NULL
               GROUP BY title HAVING count(*) = 1""").fetchall()},
    }


def _person(data: dict[str, Any], person_id: int | None, label: str | None) -> str | None:
    if person_id:
        return f"person:{person_id}"
    if label and not unnamed_speaker(label) and label.strip().lower() in data["titles"]:
        return f"person:{data['titles'][label.strip().lower()]}"
    return None


def project_meeting(conn: psycopg.Connection, meeting_id: int, index: list[Alias], fp: str) -> bool:
    """The meeting's final record → claims. False when nothing changed since the last time."""
    data = load_meeting(conn, meeting_id)
    m = data["meeting"]
    source_hash = digest(PROJECTOR, fp, {k: v for k, v in data.items() if k != "titles"})
    if stored_hash(conn, "meeting", m["code"]) == source_hash:
        return False
    day = kst_date(m["starts_at"])
    src = Source("meeting", m["code"], meeting_id)
    meeting = src.node(f"meeting:{m['code']}", "meeting", m["title"],
                       {"id": m["code"], "date": day, "subject": m["subject"], "topics": m["topics"], "summary": m["summary"]})
    src.link_names(meeting, " ".join([m["title"], m["subject"] or "", *m["topics"]]), index, "CONCERNS", stated_at=day)
    for d in data["decisions"]:
        ref = decision_ref(m["code"], d["seq"])
        node = src.node(f"decision:{ref}", "decision", d["statement"],
                        {"ref": ref, "kind": d["kind"], "statement": d["statement"], "rationale": d["rationale"],
                         "outcome": d["outcome"], "speaker": d["speaker_label"], "meeting": m["code"], "date": day})
        src.claim(node, "DECIDED_IN", meeting, qualifiers={"kind": d["kind"]}, stated_at=day)
        if d["supersedes"]:
            src.claim(node, "SUPERSEDES", f"decision:{d['supersedes']}", stated_at=day)
        if d["resolves"]:
            src.claim(f"decision:{d['resolves']}", "RESOLVES", node, stated_at=day)
        if who := _person(data, d["speaker_id"], d["speaker_label"]):
            src.claim(node, "STATED_BY", who, stated_at=day)
        src.link_names(node, f"{d['statement']} {d['rationale'] or ''}", index, "CONCERNS", stated_at=day)
    for p in data["premises"]:
        ref = f"{decision_ref(m['code'], p['decision_seq'])}-P{p['seq']}"
        node = src.node(f"premise:{ref}", "premise", p["statement"],
                        {"ref": ref, "kind": p["kind"], "statement": p["statement"], "review_when": p["review_when"], "status": p["status"]})
        src.claim(f"decision:{decision_ref(m['code'], p['decision_seq'])}", "RESTS_ON", node,
                  qualifiers={"kind": p["kind"], "status": p["status"]}, stated_at=day)
        if who := _person(data, p["speaker_id"], None):
            src.claim(node, "STATED_BY", who, stated_at=day)
        src.link_names(node, p["statement"], index, "CONCERNS", stated_at=day)
    for s in data["signals"]:
        # The decision watch's judgement of one news item against one premise: an assessed influence.
        article = src.node(f"article:{s['source_ref']}", "article", s["headline"],
                           {"id": s["source_ref"], "date": str(s["source_date"]) if s["source_date"] else None, "outlet": s["source_name"]},
                           meeting=False)
        premise = f"premise:{decision_ref(m['code'], s['decision_seq'])}-P{s['premise_seq']}"
        src.claim(article, "AFFECTS", premise, cls="assessed", extractor="decision-watch", context=s["reason"] or "",
                  stated_at=str(s["source_date"]) if s["source_date"] else day,
                  qualifiers={"effect": s["effect"], "judged": s["judged"], "reason": s["reason"],
                              "signal_status": s["status"], "signal_id": f"SIG-{s['id']}"})
    for a in data["items"]:
        node = src.node(f"action_item:{a['code']}", "action_item", a["text"],
                        {"id": a["code"], "text": a["text"], "owner": a["owner"], "due": str(a["due_date"]) if a["due_date"] else None,
                         "status": a["status"], "meeting": m["code"]})
        src.claim(node, "RAISED_IN", meeting, stated_at=day)
        if a["decision_seq"]:
            src.claim(node, "FOLLOWS_UP", f"decision:{decision_ref(m['code'], a['decision_seq'])}", stated_at=day)
        owner = f"person:{a['owner_person_id']}" if a["owner_person_id"] else (f"unit:{a['owner_unit_id']}" if a["owner_unit_id"] else None)
        if owner:
            src.claim(node, "ASSIGNED_TO", owner, stated_at=day,
                      qualifiers={"status": a["status"], "due": str(a["due_date"]) if a["due_date"] else None,
                                  "completed_at": a["completed_at"].astimezone(KST).date().isoformat() if a["completed_at"] else None})
        src.link_names(node, a["text"], index, "CONCERNS", stated_at=day)
    for p in data["participants"]:
        src.claim(f"person:{p['person_id']}", "ATTENDED", meeting, qualifiers={"role": p["role"], "attended": p["attended"]},
                  stated_at=day)
    write_source(conn, src, source_hash)
    return True


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------


def scoped_events(news: psycopg.Connection, anchors: list[str], since: datetime) -> list[str]:
    """News events worth a place in the graph: any registered article (the system's own data), or an article in the
    window whose title or description names an anchor (a company of the system's data or one its meetings discuss)."""
    patterns = [f"%{a.lower()}%" for a in anchors if len(a) >= 2]
    rows = news.execute(
        """SELECT DISTINCT ea.event_id::text AS id FROM news_event_articles ea JOIN news_articles a ON a.id = ea.article_id
           WHERE a.fetch_status = 'imported'
              OR (a.published_at >= %s AND lower(a.title || ' ' || a.summary) LIKE ANY(%s))
           ORDER BY 1""", (since, patterns)).fetchall()
    return [r["id"] for r in rows]


def load_event(news: psycopg.Connection, event_id: str) -> dict[str, Any] | None:
    event = news.execute("SELECT id::text AS id, title, first_published, last_published FROM news_events WHERE id = %s::uuid",
                         (event_id,)).fetchone()
    if event is None:
        return None
    articles = news.execute(
        """SELECT a.id, a.url, a.title, a.summary, a.body, a.usage_notice, a.fetch_status, a.published_at, o.name AS outlet
           FROM news_event_articles ea JOIN news_articles a ON a.id = ea.article_id JOIN news_outlets o ON o.domain = a.outlet_domain
           WHERE ea.event_id = %s::uuid ORDER BY a.published_at, a.id""", (event_id,)).fetchall()
    ids = [a["id"] for a in articles]
    analysis: dict[str, dict[str, set[str]]] = {i: {"entities": set(), "facts": set()} for i in ids}
    for r in news.execute("SELECT article_id, entities, key_facts FROM news_article_analysis WHERE article_id = ANY(%s)", (ids,)):
        analysis[r["article_id"]]["entities"].update(str(e) for e in r["entities"] or [] if str(e).strip())
        analysis[r["article_id"]]["facts"].update(str(f) for f in r["key_facts"] or [] if str(f).strip())
    seeded: dict[str, list[str]] = {i: [] for i in ids}
    for r in news.execute("""SELECT m.article_id, m.surface FROM news_mentions m JOIN news_articles a ON a.id = m.article_id
                             WHERE m.article_id = ANY(%s) AND a.fetch_status = 'imported' ORDER BY m.surface""", (ids,)):
        seeded[r["article_id"]].append(r["surface"])
    out = []
    for a in articles:
        out.append({"id": a["id"], "url": a["url"], "title": a["title"], "summary": a["summary"], "body": a["body"],
                    "usage_notice": a["usage_notice"], "outlet": a["outlet"],
                    "date": a["published_at"].astimezone(KST).date().isoformat() if a["published_at"] else None,
                    "entities": sorted(analysis[a["id"]]["entities"]), "key_facts": sorted(analysis[a["id"]]["facts"]),
                    "seeded_names": seeded[a["id"]]})
    return {"event": dict(event), "articles": out}


@dataclass
class Budget:
    calls: int
    used: int = 0
    failed: int = 0

    def take(self) -> bool:
        if self.used >= self.calls:
            return False
        self.used += 1
        return True


def _extraction(conn: psycopg.Connection, data: dict[str, Any], ask: Ask | None, budget: Budget) -> dict[str, Any] | None:
    """The event's extraction: cached, or asked now when a model and budget are there, else None (asked next time)."""
    event, articles = data["event"], data["articles"]
    h = extract.input_hash(event, articles)
    row = conn.execute("SELECT output FROM kg_extractions WHERE event_id = %s AND extractor = %s AND input_hash = %s",
                       (event["id"], extract.EXTRACTOR, h)).fetchone()
    if row:
        return row["output"]
    if ask is None or not budget.take():
        return None
    try:
        output = extract.parse(ask(extract.SYSTEM, extract.prompt(event, articles)), [a["id"] for a in articles])
    except Exception:  # an unreadable answer or a model that is down: the event waits for the next pass
        budget.failed += 1
        return None
    conn.execute("INSERT INTO kg_extractions (event_id, extractor, input_hash, output) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                 (event["id"], extract.EXTRACTOR, h, Jsonb(output)))
    return output


def project_news_event(conn: psycopg.Connection, data: dict[str, Any], index: list[Alias], fp: str, ask: Ask | None,
                       budget: Budget) -> str:
    """unchanged | updated | extracted. An event whose extraction is still missing is projected without it and
    tried again in a later pass (its hash says so)."""
    event, articles = data["event"], data["articles"]
    base = digest(PROJECTOR, extract.EXTRACTOR, fp, data)
    current = stored_hash(conn, "news_event", event["id"])
    if current == f"{base}|x" or (current == f"{base}|-" and (ask is None or budget.used >= budget.calls)):
        return "unchanged"
    extraction = _extraction(conn, data, ask, budget)
    src = Source("news_event", event["id"])
    ev = src.node(f"event:{event['id']}", "news_event", event["title"],
                  {"id": event["id"], "first_published": kst_date(event["first_published"]),
                   "last_published": kst_date(event["last_published"]), "articles": len(articles)}, meeting=False)
    for a in articles:
        art = src.node(f"article:{a['id']}", "article", a["title"],
                       {"id": a["id"], "date": a["date"], "outlet": a["outlet"], "url": a["url"], "event": event["id"]}, meeting=False)
        src.claim(art, "PART_OF", ev, stated_at=a["date"], meeting=False)
        names = {n: ("organization", "seed") for n in a["seeded_names"]}
        names.update({n: (None, "analysis") for n in a["entities"] if n not in names})
        for name, (type_, how) in names.items():
            src.claim(art, "MENTIONS", src.mention(art, name, extractor=how, type_guess=type_, context=a["title"]),
                      extractor=how, stated_at=a["date"], meeting=False)
        src.link_names(art, f"{a['title']} {a['summary']}", index, "MENTIONS", stated_at=a["date"], meeting=False)
    if extraction:
        dates = {a["id"]: a["date"] for a in articles}
        latest = max((d for d in dates.values() if d), default=None)
        named = {}
        for e in extraction["entities"]:
            key = src.mention(ev, e["name"], extractor=extract.EXTRACTOR, type_guess=e["type"], aliases=e["aliases"],
                              context=event["title"])
            for n in (e["name"], *e["aliases"]):
                named[n] = key
            src.claim(ev, "MENTIONS", key, extractor=extract.EXTRACTOR, stated_at=latest, meeting=False)
        for r in extraction["relations"]:
            when = r["date"] or max((dates[i] for i in r["article_ids"] if dates.get(i)), default=latest)
            src.claim(named[r["subject"]], r["predicate"], named[r["object"]], cls="reported", extractor=extract.EXTRACTOR,
                      event_ref=event["id"], stated_at=when, context=r["evidence"], meeting=False,
                      qualifiers={"status": r["status"], "expression": r["expression"], "article_ids": r["article_ids"]},
                      local=f"{r['status']}|{when}|{','.join(r['article_ids'])}|{r['expression']}")
    write_source(conn, src, f"{base}|{'x' if extraction else '-'}")
    return "extracted" if extraction else "updated"
