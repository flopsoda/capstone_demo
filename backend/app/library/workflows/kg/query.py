"""What the Advisory Agent reads from the Enterprise KG: one or more entities, and what the meetings and the news say
about them — kept in separate parts so that an association is never read as an influence:

    decisions / follow_ups   the meeting records about the entity (structural: the meeting DB says so)
    reported_facts           relations the news states (per news event, with status over time and the reports behind)
    assessed_impacts         a news item the decision watch judged to shake a decision's premise (assessed)
    associations             a news event and a decision that only share the entity — not an influence judgement

Access: the meeting store's rule (meeting_store.VISIBLE) on every record and every claim that came from a meeting,
inside the SQL, before anything is read. News is public, as in the news Tools.
"""

from __future__ import annotations

from typing import Any

import psycopg

from ..meeting_store import VISIBLE, Viewer
from . import project
from .resolve import normalize

MAX_DECISIONS = 12
MAX_REPORTED = 15
MAX_EVENTS = 10
MAX_ASSOCIATIONS = 8

LEGEND = ("decisions · follow_ups는 회의 기록(확정 회의록)이 직접 담은 사실, reported_facts는 기사가 전한 관계(사건별, 기사에서 추출), "
          "assessed_impacts는 결정 감시가 기사와 결정의 전제를 대조해 판정한 영향, associations는 같은 기업 · 인물을 다룰 뿐 "
          "영향 여부를 판정하지 않은 연결이다.")


def _visible(alias: str) -> str:
    return (f"({alias}.meeting_id IS NULL OR EXISTS (SELECT 1 FROM meetings m WHERE m.id = {alias}.meeting_id AND "
            + VISIBLE + "))")


def facts_about(conn: psycopg.Connection, viewer: Viewer, node_ids: list[str] | set[str]) -> list[dict[str, Any]]:
    """Visible facts touching any of `node_ids`, with both ends: both ends visible and at least one visible claim."""
    if not node_ids:
        return []
    return [dict(r) for r in conn.execute(
        f"""SELECT f.*, s.type AS s_type, s.label AS s_label, s.attrs AS s_attrs, o.type AS o_type, o.label AS o_label, o.attrs AS o_attrs
            FROM kg_facts f JOIN kg_nodes s ON s.id = f.subject JOIN kg_nodes o ON o.id = f.object
            WHERE (f.subject = ANY(%(ids)s) OR f.object = ANY(%(ids)s)) AND {_visible('s')} AND {_visible('o')}
              AND EXISTS (SELECT 1 FROM kg_fact_support fs JOIN kg_claims c ON c.id = fs.claim_id
                          WHERE fs.fact_id = f.id AND {_visible('c')})""",
        {"ids": list(node_ids), **viewer.scope()}).fetchall()]


def find_entities(conn: psycopg.Connection, query: str, names: list[str] | None) -> tuple[list[dict[str, Any]], list[str]]:
    """(entities, candidate names): the names asked for, else the known names the query uses."""
    ids: list[str] = []
    candidates: list[str] = []
    for name in names or []:
        key = normalize(name)
        rows = conn.execute("SELECT DISTINCT node_id FROM kg_aliases WHERE alias_norm = %s", (key,)).fetchall() if key else []
        if rows:
            ids.extend(r["node_id"] for r in rows)
        elif key:
            candidates.extend(r["label"] for r in conn.execute(
                """SELECT DISTINCT n.label, similarity(a.alias_norm, %s) AS s FROM kg_aliases a JOIN kg_nodes n ON n.id = a.node_id
                   WHERE a.alias_norm %% %s ORDER BY s DESC LIMIT 3""", (key, key)))
    if not ids:
        ids = [a.node_id for _, a in project.link_text(query, project.alias_index(conn))]
    ids = list(dict.fromkeys(ids))
    rows = conn.execute("SELECT id, type, label, attrs FROM kg_nodes WHERE id = ANY(%s) AND kind = 'entity'", (ids,)).fetchall()
    order = {i: n for n, i in enumerate(ids)}
    entities = sorted(({"id": r["id"], "name": r["label"], "type": r["type"], "aliases": r["attrs"].get("aliases", [])} for r in rows),
                      key=lambda e: order[e["id"]])
    return entities, list(dict.fromkeys(candidates))


def _supports(conn: psycopg.Connection, viewer: Viewer, fact_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    if not fact_ids:
        return out
    for r in conn.execute(
            f"""SELECT fs.fact_id, c.qualifiers, c.context, c.stated_at FROM kg_fact_support fs JOIN kg_claims c ON c.id = fs.claim_id
                WHERE fs.fact_id = ANY(%(ids)s) AND {_visible('c')} ORDER BY c.stated_at NULLS LAST""",
            {"ids": fact_ids, **viewer.scope()}):
        out.setdefault(r["fact_id"], []).append(r)
    return out


def query_graph(conn: psycopg.Connection, viewer: Viewer, query: str, names: list[str] | None = None) -> dict[str, Any]:
    entities, candidates = find_entities(conn, query, names)
    as_of = conn.execute("SELECT max(synced_at) AS t FROM kg_sources").fetchone()["t"]
    base = {"query": query, "as_of": as_of.isoformat(timespec="minutes") if as_of else None, "legend": LEGEND}
    if not entities:
        note = ("지식 그래프에서 해당 기업 · 인물을 찾지 못했습니다." + (f" 비슷한 이름: {', '.join(candidates)}" if candidates else "")
                + (" 그래프가 아직 만들어지지 않았습니다." if as_of is None else ""))
        return {**base, "found": False, "entities": [], "note": note}
    focus = {e["id"] for e in entities}
    label = {e["id"]: e["name"] for e in entities}
    first = facts_about(conn, viewer, focus)

    # Meeting side: records that discuss the entity → their meetings → every decision, premise and item of those meetings.
    concern = [f for f in first if f["predicate"] == "CONCERNS"]
    direct = {f["subject"] for f in concern}
    meetings = {f["subject"] for f in concern if f["s_type"] == "meeting"}
    meetings |= {f"meeting:{f['s_attrs']['meeting']}" for f in concern if f["s_attrs"].get("meeting")}
    premise_hits = [f["subject"] for f in concern if f["s_type"] == "premise"]
    for f in facts_about(conn, viewer, premise_hits):
        if f["predicate"] == "RESTS_ON":
            meetings.add(f"meeting:{f['s_attrs']['meeting']}")
    around_meetings = facts_about(conn, viewer, meetings)
    decision_ids = {f["subject"] for f in around_meetings if f["predicate"] == "DECIDED_IN"}
    item_ids = {f["subject"] for f in around_meetings if f["predicate"] == "RAISED_IN"}
    around_records = facts_about(conn, viewer, decision_ids | item_ids)
    premises: dict[str, list[dict[str, Any]]] = {}
    premise_ids = set()
    for f in around_records:
        if f["predicate"] == "RESTS_ON":
            premise_ids.add(f["object"])
            premises.setdefault(f["subject"], []).append({"ref": f["o_attrs"].get("ref"), "kind": f["o_attrs"].get("kind"),
                                                          "statement": f["o_attrs"].get("statement"),
                                                          "status": f["o_attrs"].get("status"), "review_when": f["o_attrs"].get("review_when")})
    by_meeting = {f["object"]: f for f in around_meetings if f["predicate"] == "DECIDED_IN"}
    meeting_info = {f["object"]: {"id": f["o_attrs"].get("id"), "title": f["o_label"], "date": f["o_attrs"].get("date"),
                                  "subject": f["o_attrs"].get("subject")} for f in around_meetings}
    stated = {f["subject"]: f["o_label"] for f in around_records if f["predicate"] == "STATED_BY"}
    decisions = []
    for f in sorted((f for f in around_meetings if f["predicate"] == "DECIDED_IN"),
                    key=lambda f: (f["s_attrs"].get("date") or "", f["s_attrs"].get("ref") or ""), reverse=True):
        a = f["s_attrs"]
        decisions.append({"ref": a.get("ref"), "kind": a.get("kind"), "statement": a.get("statement"),
                          "meeting": meeting_info.get(f["object"]), "status": f["status"],
                          "superseded_by": (f["attrs"].get("superseded_by") or "").removeprefix("decision:") or None,
                          "about_entity": f["subject"] in direct, "stated_by": stated.get(f["subject"]) or a.get("speaker"),
                          "premises": premises.get(f["subject"], [])})
    decisions = decisions[:MAX_DECISIONS]
    owners = {f["subject"]: f for f in around_records if f["predicate"] == "ASSIGNED_TO"}
    follow_ups = []
    for f in around_meetings:
        if f["predicate"] != "RAISED_IN":
            continue
        a, owner = f["s_attrs"], owners.get(f["subject"])
        follow_ups.append({"id": a.get("id"), "text": a.get("text"), "owner": owner["o_label"] if owner else a.get("owner"),
                           "due": a.get("due"), "status": a.get("status"), "meeting": a.get("meeting")})
    follow_ups.sort(key=lambda x: (x["status"] in ("done", "cancelled"), x["due"] or "9999"))

    # News side: articles and events that name the entity, and the relations the news states about it.
    mentioning = [f for f in first if f["predicate"] == "MENTIONS"]
    articles = {f["subject"] for f in mentioning if f["s_type"] == "article"}
    events = {f["subject"] for f in mentioning if f["s_type"] == "news_event"}
    around_articles = facts_about(conn, viewer, articles)
    events |= {f["object"] for f in around_articles if f["predicate"] == "PART_OF"}
    event_rows = conn.execute("SELECT id, label, attrs FROM kg_nodes WHERE id = ANY(%s)", (list(events),)).fetchall()
    event_info = {r["id"]: {"id": r["attrs"].get("id"), "title": r["label"], "first_published": r["attrs"].get("first_published"),
                            "last_published": r["attrs"].get("last_published"), "articles": r["attrs"].get("articles")} for r in event_rows}
    reported = [f for f in first if f["fact_class"] == "reported"]
    reported.sort(key=lambda f: (f["last_seen"] or "", f["id"]), reverse=True)
    reported = reported[:MAX_REPORTED]
    supports = _supports(conn, viewer, [f["id"] for f in reported])
    article_rows = {r["id"]: r for r in conn.execute("SELECT id, label, attrs FROM kg_nodes WHERE type = 'article' AND id = ANY(%s)",
                                                      ([f"article:{i}" for f in reported for c in supports.get(f["id"], [])
                                                        for i in (c["qualifiers"] or {}).get("article_ids", [])],)).fetchall()}
    reported_facts = []
    for f in reported:
        reports = []
        for c in supports.get(f["id"], []):
            for aid in (c["qualifiers"] or {}).get("article_ids", []):
                r = article_rows.get(f"article:{aid}")
                if r and aid not in {x["article_id"] for x in reports}:
                    reports.append({"article_id": aid, "date": r["attrs"].get("date"), "outlet": r["attrs"].get("outlet"),
                                    "title": r["label"], "url": r["attrs"].get("url")})
        reported_facts.append({
            "subject": f["s_label"], "predicate": f["predicate"], "object": f["o_label"], "status": f["status"],
            "valid_from": _iso(f["valid_from"]), "valid_to": _iso(f["valid_to"]),
            "expressions": f["attrs"].get("expressions", []), "timeline": f["attrs"].get("timeline", []),
            "event": event_info.get(f"event:{f['instance_key']}", {"id": f["instance_key"]}),
            "reports": reports, "evidence": [c["context"] for c in supports.get(f["id"], []) if c["context"]][:3]})

    # Assessed: a news item judged against a premise of a decision here (or of a decision about the entity).
    impacts_raw = [f for f in facts_about(conn, viewer, premise_ids | articles) if f["predicate"] == "AFFECTS"]
    premise_owner = {f["object"]: f for f in around_records if f["predicate"] == "RESTS_ON"}
    assessed = []
    judged_pairs = set()
    for f in impacts_raw:
        owner = premise_owner.get(f["object"])
        a = f["attrs"]
        decision = owner["s_attrs"] if owner else {}
        article_event = next((x["object"] for x in around_articles if x["subject"] == f["subject"] and x["predicate"] == "PART_OF"), None)
        judged_pairs.add((article_event, owner["subject"] if owner else None))
        assessed.append({"effect": a.get("effect"), "judged": a.get("judged"), "reason": a.get("reason"), "status": f["status"],
                         "signal": a.get("signal_id"),
                         "news": {"id": f["s_attrs"].get("id"), "title": f["s_label"], "date": f["s_attrs"].get("date"),
                                  "outlet": f["s_attrs"].get("outlet")},
                         "premise": {"ref": f["o_attrs"].get("ref"), "statement": f["o_attrs"].get("statement"),
                                     "status": f["o_attrs"].get("status")},
                         "decision": {"ref": decision.get("ref"), "statement": decision.get("statement"),
                                      "meeting": decision.get("meeting")}})

    # Associations: the entity's latest news events next to decisions that discuss the entity, unless judged.
    about = [d for d in decisions if d["status"] == "active" and (d["about_entity"] or (d["meeting"] or {}).get("id"))]
    associations = []
    for ev_id, ev in sorted(event_info.items(), key=lambda kv: kv[1].get("last_published") or "", reverse=True)[:MAX_EVENTS]:
        for d in about:
            if (ev_id, f"decision:{d['ref']}") in judged_pairs:
                continue
            associations.append({"news_event": ev, "decision": {"ref": d["ref"], "statement": d["statement"], "meeting": d["meeting"]},
                                 "via": ", ".join(label[i] for i in focus), "basis": "같은 엔티티를 다룸 — 영향 판정 아님"})
            if len(associations) >= MAX_ASSOCIATIONS:
                break
        if len(associations) >= MAX_ASSOCIATIONS:
            break

    meeting_list = sorted((m for m in meeting_info.values() if m.get("id")), key=lambda m: m.get("date") or "", reverse=True)
    return {**base, "found": True, "entities": entities,
            "meetings": meeting_list, "decisions": decisions, "follow_ups": follow_ups[:15],
            "reported_facts": reported_facts, "assessed_impacts": assessed, "associations": associations,
            "news_events": sorted(event_info.values(), key=lambda e: e.get("last_published") or "", reverse=True)[:MAX_EVENTS]}


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value
