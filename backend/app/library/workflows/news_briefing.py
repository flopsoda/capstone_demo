"""News Briefing — the briefing view of the news pipeline (library/workflows/news/), one run per request.

    query (any: a company, an industry, a market, a regulation, a person)
      live : news.pipeline.gather — collect what is due from the publisher feeds, fetch the pages that
             matter, then reuse everything the system's news store holds; duplicates out, events
             (persisted: an article keeps its event id)
      demo : the scenario's fictional articles (FixtureSource), grouped the same way, in memory
      then : every event (topic) scored for importance (independent coverage, recency, how squarely it is
             about the query) → news.analysis: each kept article's stance toward the target, type, risk and
             facts (cached per version in the live store), then each event's summary, evidence-backed facts,
             impact, implication → outlet tone from the articles' stances → a structured result

`purpose` selects optional analysis steps on top of the general ones. "general" is the default;
"risk" adds a risk category, level and check points per topic.

The workflow covers external news only: linking a story to the company's own records is the
Super Agent's job (it has both answers), not this workflow's.

Not agent-visible, no LangChain dependency: the Tool passes `llm` as (system, user) -> text.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from .news import analysis, cluster
from .news.feeds import KST, Feed
from .news.fetch import Fetcher
from .news.pipeline import REQUEST_BUDGET, Budget, gather, lead_of
from .news.store import NewsStore
from .news.text import target as target_of
from .news.text import terms as query_terms

LLM = Callable[[str, str], str]
Log = Callable[[str, dict], None]

PURPOSES = ("general", "risk")
TEXT_FOR_LLM = 600  # characters of an article's text the analysis reads
ARTICLES_PER_TOPIC = 6  # articles of a topic the article-level analysis reads


# ── Articles ────────────────────────────────────────────────────────────────

@dataclass
class Article:
    id: str
    title: str
    summary: str
    url: str
    outlet: str
    date: str  # ISO date (Korean time)
    source: str  # demo_fixture | news_store
    hint: dict[str, Any] | None = None  # labels a source already carries (the demo fixture's risk)
    content_level: str = "snippet"
    body: str = ""
    usage_notice: bool = False
    published: datetime | None = None
    body_hash: str | None = None
    event_id: str | None = None
    syndicated_from: str | None = None  # another outlet's story this article ran

    @property
    def reporter(self) -> str:
        """Whose reporting it is (a wire story counts for its agency, a copy for nobody new)."""
        return cluster.independent_outlet(self.outlet, (self.body or self.summary)[:80])

    def text_for_analysis(self) -> tuple[str, str]:
        """(text, basis): the body when there is one — only its lead under an "AI 학습·활용 금지"
        notice — else the description, else the headline."""
        if self.body and not self.usage_notice:
            return self.body[:TEXT_FOR_LLM], "body"
        if self.body:
            return self.body[:200], "lead"
        if self.summary:
            return self.summary[:300], "snippet"
        return self.title, "title"

    def item(self) -> cluster.Item:
        published = self.published or datetime.fromisoformat(self.date).replace(tzinfo=KST)
        return cluster.Item(self.id, self.title, self.outlet, published, self.url,
                            (self.body or self.summary)[:200], self.body_hash, self.event_id, self.hint, bool(self.body))


class Source(Protocol):
    name: str

    def search(self, query: str, since: date) -> list[Article]: ...


class FixtureSource:
    """The demo scenario's fictional articles (data/news.json): the only source in demo mode. An article
    matches when the query names one of its companies, or when every query word appears in it."""

    name = "demo_fixture"

    def __init__(self, path: Path):
        self.path = path

    def _articles(self) -> list[dict[str, Any]]:
        return json.loads(self.path.read_text(encoding="utf-8"))["articles"] if self.path.exists() else []

    def latest(self) -> date | None:
        """The scenario's last day: demo runs use it as today (the scenario is set in a fixed time)."""
        dates = [a["date"] for a in self._articles()]
        return date.fromisoformat(max(dates)) if dates else None

    def search(self, query: str, since: date) -> list[Article]:
        terms, q = query_terms(query), query.lower()
        out = []
        for a in self._articles():
            if date.fromisoformat(a["date"]) < since:
                continue
            text = " ".join([a["headline"], a["summary"], *a.get("companies", [])]).lower()
            named = any(c.lower() in q for c in a.get("companies", []))
            if named or (terms and all(t in text for t in terms)):
                out.append(Article(a["id"], a["headline"], a["summary"], a.get("url", ""), a["source"], a["date"],
                                   self.name, {"risk": a.get("risk"), "key_facts": a.get("key_facts", []),
                                               "companies": a.get("companies", []), "issue": a.get("issue")},
                                   content_level="snippet"))
        return out


def article_from_row(row: dict[str, Any]) -> Article:
    published = row["published_at"].astimezone(KST)
    return Article(row["id"], row["title"], row["summary"] or lead_of(row), row["url"], row["outlet"],
                   published.date().isoformat(), "news_store", content_level=row["content_level"], body=row["body"],
                   usage_notice=row["usage_notice"], published=published, body_hash=row["body_hash"],
                   event_id=str(row["event_id"]) if row.get("event_id") else None, syndicated_from=row.get("syndicated_from"))


# ── Filter: importance ──────────────────────────────────────────────────────

def importance(topic: list[Article], query: str, today: date) -> float:
    """0–100 without reading the articles: how widely it was reported (independent outlets: a copy
    or a wire story run elsewhere is not more coverage), how recent it is, and whether the query is
    the subject of the headline rather than a passing mention."""
    outlets = len(independent_outlets(topic))
    newest = max(date.fromisoformat(a.date) for a in topic)
    age = max(0, (today - newest).days)
    terms = query_terms(query)
    # Share of the query's words in each headline: "삼성전자 노조" scores a headline about Samsung's
    # union above one that names Samsung in passing.
    in_title = sum(sum(t in a.title.lower() for t in terms) / len(terms) for a in topic) / len(topic) if terms else 0
    score = min(outlets, 5) * 10 + max(0, 30 - age * 4) + in_title * 20
    return round(min(100.0, score), 1)


def independent_outlets(topic: list[Article]) -> set[str]:
    return {a.reporter for a in topic if not a.syndicated_from}


def evidence(topic: list[Article]) -> dict[str, int]:
    """How much of a topic the analysis could read: articles with a body, a description only, a headline only."""
    out = {"body": 0, "snippet": 0, "title": 0}
    for a in topic:
        out["body" if a.content_level in ("full", "partial") else a.content_level if a.content_level in out else "title"] += 1
    return out


def adjusted_importance(t: dict[str, Any]) -> float:
    """The filter's score, then what the content says: the model's own rating and, for
    purpose="risk", a high risk. A positive/neutral topic is not pushed up by its risk label."""
    score = t["importance"] + {"high": 15, "low": -10}.get(t.get("importance_llm", ""), 0)
    risk = t.get("risk")
    if risk and risk["level"] == "HIGH":
        score += 15
    return round(max(0.0, min(100.0, score)), 1)


# ── Brief: events → result ──────────────────────────────────────────────────

def _article_out(a: Article) -> dict[str, Any]:
    text, basis = a.text_for_analysis()
    out = {"id": a.id, "title": a.title, "summary": a.summary, "outlet": a.outlet, "date": a.date,
           "content_level": a.content_level, "text": text, "basis": basis}
    if a.url:
        out["url"] = a.url
    if a.syndicated_from:
        out["syndicated_from"] = a.syndicated_from
    return out


def article_analyses(topics: list[dict[str, Any]], by_id: dict[str, Article], target: str, llm: LLM | None,
                     store: NewsStore | None) -> dict[str, dict[str, Any]]:
    """Per-article analysis of the kept topics' articles: the fixture's labels (demo), the store's earlier
    analysis of the same version (live), the model for the rest, rules when there is none."""
    wanted = [a for t in topics for a in t["articles"][:ARTICLES_PER_TOPIC]]
    out: dict[str, dict[str, Any]] = {}
    for a in wanted:
        hint = by_id[a["id"]].hint
        if hint is not None:
            out[a["id"]] = analysis.fixture_article(a, hint)
    out.update(analysis.analyze_with_store([a for a in wanted if a["id"] not in out], target, llm, store))
    return out


def brief(articles: list[Article], assignment: dict[str, str], query: str, *, purpose: str, today: date, since: date,
          limit: int, llm: LLM | None, log: Log, store: NewsStore | None = None) -> dict[str, Any]:
    by_id = {a.id: a for a in articles}
    target = target_of(query)
    groups = cluster.groups([a.item() for a in articles], assignment)
    # Recency counts from the newest article: live news ends today, the fictional scenario ends on its
    # own last day, and either way the latest story scores as new.
    as_of = min(today, max((date.fromisoformat(a.date) for a in articles), default=today))
    topics = []
    for g in groups:
        members = sorted((by_id[i.id] for i in g), key=lambda a: (a.content_level not in ("full", "partial"), a.date, a.id))
        members.sort(key=lambda a: a.date, reverse=True)
        topic = {
            "importance": importance(members, query, as_of),
            "article_count": len(members),
            "outlet_count": len({a.outlet for a in members}),
            "independent_outlet_count": len(independent_outlets(members)),
            "evidence": evidence(members),
            "first_reported": min(a.date for a in members),
            "last_reported": max(a.date for a in members),
            "articles": [_article_out(a) for a in members],
        }
        if members[0].event_id:
            topic["event_id"] = members[0].event_id
        topics.append(topic)
    topics.sort(key=lambda t: t["last_reported"], reverse=True)  # newest first among equals
    topics.sort(key=lambda t: t["importance"], reverse=True)
    topic_count = len(topics)
    topics = topics[:limit]
    for n, t in enumerate(topics, 1):
        t["id"] = f"topic-{n}"
    log("filter", {"articles": len(articles), "topics": topic_count, "kept": len(topics)})

    analyses = article_analyses(topics, by_id, target, llm, store)
    trend, so_what, method = analysis.analyze_events(topics, analyses, query, purpose, llm)
    tone_rows = []
    for t in topics:
        t["importance"] = adjusted_importance(t)
        t["sentiment"] = analysis.SENTIMENT_OF_IMPACT[t["impact"]]
        t["key_facts"] = [f["text"] for f in t["evidence_facts"]]
        t["stance_by_outlet"], t["divergent_coverage"] = analysis.stance_by_outlet(t["articles"], analyses)
        t["timeline"] = [{"date": a["date"], "outlet": a["outlet"], "title": a["title"]}
                         for a in sorted(t["articles"], key=lambda a: a["date"])]
        for a in t["articles"]:
            a.pop("text", None)
            r = analyses.get(a["id"])
            if r:
                a.update(stance=r["stance"], article_type=r["article_type"])
                tone_rows.append((a["outlet"], r["stance"], bool(a.get("syndicated_from")) or r["article_type"] == "wire_copy"))
    topics.sort(key=lambda t: t["importance"], reverse=True)  # ids stay: the LLM answered by id
    log("analyze", {"method": method, "articles": len(analyses)})
    if store is not None:
        store.save_event_analyses({
            "event_id": t["event_id"], "target": target, "analyzer_version": analysis.EVENT_VERSION, "summary": t["summary"],
            "key_facts": t["evidence_facts"], "impact": t["impact"], "risk_category": (t.get("risk") or {}).get("category"),
            "risk_level": (t.get("risk") or {}).get("level"), "importance_llm": t.get("importance_llm"),
            "implication": t.get("implication", ""), "method": method} for t in topics if t.get("event_id"))
    levels: dict[str, int] = {}
    for a in articles:
        levels[a.content_level] = levels.get(a.content_level, 0) + 1
    return {
        "query": query,
        "purpose": purpose,
        "period": {"from": since.isoformat(), "to": today.isoformat()},
        "collected": len(articles),
        "content_levels": levels,
        "topic_count": topic_count,
        "analysis": method,
        "article_analysis": sorted({r["method"] for r in analyses.values()}),
        "trend": trend,
        "so_what": so_what,
        "topics": topics,
        "negative_alerts": [t["id"] for t in topics if t["impact"] == "negative" and t["importance"] >= 50],
        "outlet_tone": analysis.outlet_tone(tone_rows),
    }


def _no_log(stage: str, detail: dict) -> None:
    return None


def run_news_briefing(query: str, *, purpose: str = "general", days: int = 7, limit: int = 8, sources: list[Source],
                      llm: LLM | None = None, today: date | None = None, log: Log | None = None) -> dict[str, Any]:
    """Briefing over in-memory sources (the demo fixture): collect → duplicates out → group → analyze."""
    log = log or _no_log
    query = (query or "").strip()
    purpose = purpose if purpose in PURPOSES else "general"
    today = today or datetime.now(KST).date()
    if not query:
        return {"query": "", "error": "검색할 주제가 없습니다.", "topics": []}
    since = today - timedelta(days=days)
    articles: list[Article] = []
    failed: list[str] = []
    for src in sources:
        try:
            found = src.search(query, since)
        except Exception as e:  # one source down does not stop the others
            failed.append(src.name)
            log("collect", {"source": src.name, "error": type(e).__name__})
            continue
        log("collect", {"source": src.name, "count": len(found)})
        articles.extend(found)
    kept, dropped = cluster.dedupe([a.item() for a in articles])
    keep = {i.id for i in kept}
    articles = [a for a in articles if a.id in keep]
    result = brief(articles, cluster.assign(kept, query_terms(query)), query, purpose=purpose, today=today, since=since,
                   limit=limit, llm=llm, log=log)
    log("respond", {"topics": len(result["topics"])})
    return {**result, "collected": len(articles) + len(dropped), "duplicates_removed": len(dropped), "failed_sources": failed}


def run_live_briefing(query: str, *, store: NewsStore, feeds: list[Feed], fetcher: Fetcher, purpose: str = "general",
                      days: int = 7, limit: int = 8, llm: LLM | None = None, now: datetime | None = None,
                      budget: Budget = REQUEST_BUDGET, log: Log | None = None) -> dict[str, Any]:
    """Briefing over real news: gather (collect what is due, reuse the store, persisted events) → analyze."""
    log = log or _no_log
    query = (query or "").strip()
    purpose = purpose if purpose in PURPOSES else "general"
    now = now or datetime.now(KST)
    if not query:
        return {"query": "", "error": "검색할 주제가 없습니다.", "topics": []}
    found = gather(store, feeds, fetcher, query, days=days, budget=budget, now=now, log=log)
    articles = [article_from_row(r) for r in found["articles"]]
    assignment = {a.id: a.event_id for a in articles}
    result = brief(articles, assignment, query, purpose=purpose, today=now.date(), since=(now - timedelta(days=days)).date(),
                   limit=limit, llm=llm, log=log, store=store)
    log("respond", {"topics": len(result["topics"])})
    return {**result, "duplicates_removed": found["duplicates"], "freshness": found["freshness"], "coverage": found["coverage"]}


def topic_line(t: dict[str, Any]) -> str:
    """One line per topic for a plain-text briefing."""
    mood = {"negative": "부정", "positive": "긍정", "neutral": "중립"}[t["sentiment"]]
    risk = f" · {t['risk']['label']} {t['risk']['level']}" if t.get("risk") else ""
    return f"[중요도 {t['importance']:.0f} · {mood}{risk} · 기사 {t['article_count']}건/매체 {t['outlet_count']}곳] {t['summary']}"
