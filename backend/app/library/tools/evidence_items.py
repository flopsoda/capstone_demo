"""What a Tool result puts in the run's evidence ledger (runtime/evidence.py): the records it read, by the id the
model sees in the result — meeting decisions (MTG-…-D1), meetings, action items, articles, decision-watch signals,
calculated figures. Each Tool returns `recorded(result, extractor)` so the result itself is unchanged."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Any

from ...runtime import evidence

Item = dict[str, Any]


def recorded(result: Any, extract: Callable[[dict[str, Any]], Iterable[Item]]) -> Any:
    if isinstance(result, dict) and not result.get("error") and evidence.current() is not None:
        evidence.add_many(extract(result))
    return result


def _meeting(m: dict[str, Any], source: str) -> Item:
    return {"id": m.get("id"), "kind": "meeting", "title": m.get("title"), "date": m.get("date"),
            "classification": m.get("classification"), "source": source}


def _signal(s: dict[str, Any], source: str, **extra: Any) -> Item:
    return {"id": s.get("id"), "kind": "signal", "title": s.get("headline"), "date": s.get("date"), "outlet": s.get("source"),
            "text": f"{s.get('effect')} — {s.get('reason')}", "source": source, **extra}


def meeting_context(result: dict[str, Any]) -> Iterator[Item]:
    src = "run_meeting_context_retrieval"
    for m in result.get("meetings") or []:
        yield _meeting(m, src)
        base = {"meeting": m.get("id"), "meeting_title": m.get("title"), "date": m.get("date"), "classification": m.get("classification"), "source": src}
        for d in m.get("decisions") or []:
            yield {"id": d.get("ref"), "kind": "meeting_decision", "text": d.get("statement"), "speaker": d.get("speaker"), **base}
            for p in d.get("premises") or []:
                for s in p.get("signals") or []:
                    yield _signal(s, src, meeting=m.get("id"))
        for d in m.get("open_issues") or []:
            yield {"id": d.get("ref"), "kind": "meeting_issue", "text": d.get("statement"), **base}
        for a in m.get("open_action_items") or []:
            yield {"id": a.get("id"), "kind": "action_item", "text": a.get("text"), "owner": a.get("owner"), "due": a.get("due"),
                   "status": a.get("status"), **base}


def action_items(result: dict[str, Any]) -> Iterator[Item]:
    src = "run_action_item_tracking"
    for a in result.get("items") or []:
        m = a.get("meeting") or {}
        yield {"id": a.get("id"), "kind": "action_item", "text": a.get("text"), "owner": a.get("owner"), "due": a.get("due"),
               "status": a.get("status"), "note": a.get("last_note"), "meeting": m.get("id"), "meeting_title": m.get("title"),
               "date": m.get("date"), "source": src}


def _article(a: dict[str, Any], src: str, **extra: Any) -> Item:
    return {"id": a.get("id"), "kind": "article", "title": a.get("headline") or a.get("title"), "date": a.get("date"),
            "outlet": a.get("name") or a.get("outlet"), "url": a.get("url"), "source": src, **extra}


def risk_news(result: dict[str, Any]) -> Iterator[Item]:
    src = "run_company_risk_news_analysis"
    for r in result.get("risks") or []:
        yield _article({**(r.get("source") or {}), "headline": r.get("headline")}, src, text=r.get("summary"),
                       risk=f"{r.get('label')} {r.get('level')}", facts=r.get("key_facts"))
        for t in r.get("timeline") or []:
            yield _article(t, src, risk=f"{r.get('label')} {t.get('level')}")
    for p in result.get("positive_signals") or []:
        if isinstance(p.get("source"), dict):
            yield _article({**p["source"], "headline": p.get("headline")}, src, text=p.get("summary"))


def news_briefing(result: dict[str, Any]) -> Iterator[Item]:
    src = "run_news_briefing"
    for t in result.get("topics") or []:
        for a in t.get("articles") or []:
            yield _article(a, src, text=a.get("summary"))


def enterprise_kg(result: dict[str, Any]) -> Iterator[Item]:
    src = "query_enterprise_kg"
    for m in result.get("meetings") or []:
        yield _meeting(m, src)
    for d in result.get("decisions") or []:
        m = d.get("meeting") or {}
        yield {"id": d.get("ref"), "kind": "meeting_decision", "text": d.get("statement"), "speaker": d.get("stated_by"),
               "status": d.get("status"), "meeting": m.get("id"), "meeting_title": m.get("title"), "date": m.get("date"), "source": src}
    for a in result.get("follow_ups") or []:
        m = a.get("meeting") if isinstance(a.get("meeting"), dict) else {"id": a.get("meeting")}
        yield {"id": a.get("id"), "kind": "action_item", "text": a.get("text"), "owner": a.get("owner"), "due": a.get("due"),
               "status": a.get("status"), "meeting": m.get("id"), "source": src}
    for f in result.get("reported_facts") or []:
        relation = f"{f.get('subject')} —{f.get('predicate')}→ {f.get('object')} ({f.get('state')})"
        for r in f.get("reports") or []:
            yield {"id": r.get("article_id"), "kind": "article", "title": r.get("title"), "date": r.get("date"), "outlet": r.get("outlet"),
                   "url": r.get("url"), "relation": relation, "source": src}
    for a in result.get("assessed_impacts") or []:
        news = a.get("news") or {}
        if a.get("signal"):
            yield {"id": a["signal"], "kind": "signal", "title": news.get("title"), "date": news.get("date"), "outlet": news.get("outlet"),
                   "text": f"{a.get('effect')} — {a.get('reason')}", "source": src}
        if news.get("id"):
            yield {"id": news["id"], "kind": "article", "title": news.get("title"), "date": news.get("date"), "outlet": news.get("outlet"), "source": src}
    for e in result.get("news_events") or []:
        if e.get("id"):
            yield {"id": e["id"], "kind": "news_event", "title": e.get("title"), "date": e.get("last_published"), "source": src}


def analysis(result: dict[str, Any]) -> Iterator[Item]:
    for i in result.get("items") or []:
        yield {**i, "source": "run_mna_review"}
    for c in result.get("checks") or []:
        yield {"id": c["id"], "kind": "check", "label": c["criterion"], "display": c["actual"], "met": c["met"], "source": "run_mna_review"}
