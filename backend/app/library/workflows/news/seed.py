"""Initial data for a news store: articles a system registers from its data file (data/news.json)
instead of discovering them in a feed — with the events and analyses already made for them.

NewsStore(schema, seed=path) applies the file on first use in each process, after the migrations
(like the meeting store's seed). Every key is fixed, so applying it again — a restart, a second
process — updates the same rows and never adds a duplicate:

    article   its own id from the file                     news_articles (url NULL without a link)
    outlet    the file's outlet → domain map               news_outlets
    event     uuid5 of (schema, the article's "event")     news_events / news_event_articles
    analysis  (article, target, ARTICLE_VERSION) for each
              company the article is about                 news_article_analysis (method "imported")
    mention   (article, company name)                      news_mentions (entity_ref left to the KG)

Once stored, these articles are news like any other: the pipeline searches, groups and analyzes
them the same way (an article keeps the event it has; an analysis of the same version is reused).

File format: {"outlets": {name: domain}, "articles": [{"id", "date" (YYYY-MM-DD, Korean time),
"source" (outlet name), "headline", "summary", "companies": [names], "event": key,
"risk": {category, label, level} | null, "key_facts": [..], "sentiment"?: positive|negative|neutral,
"url"?: original link}]}.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .analysis import ARTICLE_VERSION
from .extract import SNIPPET_CHARS
from .feeds import KST
from .labels import article_type
from .store import refresh_events, squeeze
from .text import target

PUBLISHED_AT = time(9, 0)  # a dated article without a time: morning, Korean time
_STANCE_OF_SENTIMENT = {"positive": "favorable", "negative": "critical", "neutral": "neutral"}


def event_id(schema: str, key: str) -> str:
    """The same event key is the same event id in any database."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"news-seed:{schema}:{key}"))


def _stance(a: dict[str, Any]) -> str:
    """The analysis the file carries: an article reporting a risk is critical of its company; one
    without a risk reports in the way its sentiment says (good news unless stated otherwise)."""
    if a.get("risk"):
        return "critical"
    return _STANCE_OF_SENTIMENT.get(a.get("sentiment", "positive"), "neutral")


def apply_seed(conn: psycopg.Connection, schema: str, data: dict[str, Any]) -> dict[str, int]:
    """Upserts the file's articles, events, analyses and mentions. Returns counts of what it wrote."""
    outlets = data.get("outlets", {})
    articles = data.get("articles", [])
    for name, domain in outlets.items():
        conn.execute("INSERT INTO news_outlets (domain, name) VALUES (%s, %s) ON CONFLICT (domain) DO UPDATE SET name = EXCLUDED.name",
                     (domain, name))
    events: dict[str, str] = {}
    analyses = mentions = 0
    for a in sorted(articles, key=lambda a: (a["date"], a["id"])):
        if a["source"] not in outlets:
            raise ValueError(f"news seed: outlet {a['source']!r} of {a['id']} has no domain in \"outlets\"")
        published = datetime.combine(date.fromisoformat(a["date"]), PUBLISHED_AT, KST)
        summary = a.get("summary", "")
        conn.execute(
            """INSERT INTO news_articles (id, url, outlet_domain, title, summary, content_level, published_at, fetch_status)
               VALUES (%(id)s, %(url)s, %(domain)s, %(title)s, %(summary)s, %(level)s, %(published)s, 'imported')
               ON CONFLICT (id) DO UPDATE SET url = EXCLUDED.url, outlet_domain = EXCLUDED.outlet_domain, title = EXCLUDED.title,
                   summary = EXCLUDED.summary, published_at = EXCLUDED.published_at""",
            {"id": a["id"], "url": a.get("url"), "domain": outlets[a["source"]], "title": a["headline"], "summary": summary,
             "level": "snippet" if len(summary) >= SNIPPET_CHARS else "title", "published": published})
        key = a.get("event") or a["id"]
        if key not in events:
            events[key] = event_id(schema, key)
            conn.execute("INSERT INTO news_events (id, title) VALUES (%s, %s) ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title",
                         (events[key], a["headline"]))  # the event's first article (oldest first)
        conn.execute("""INSERT INTO news_event_articles (article_id, event_id) VALUES (%s, %s)
                        ON CONFLICT (article_id) DO UPDATE SET event_id = EXCLUDED.event_id""", (a["id"], events[key]))

        risk = a.get("risk") or {}
        companies = a.get("companies", [])
        text = squeeze(f"{a['headline']} {summary}")
        for t in sorted({target(c) for c in companies} - {""}):
            conn.execute(
                """INSERT INTO news_article_analysis (article_id, target, analyzer_version, stance, article_type,
                       risk_category, risk_level, key_facts, entities, basis, method)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'snippet', 'imported')
                   ON CONFLICT (article_id, target, analyzer_version) DO UPDATE SET stance = EXCLUDED.stance,
                       article_type = EXCLUDED.article_type, risk_category = EXCLUDED.risk_category, risk_level = EXCLUDED.risk_level,
                       key_facts = EXCLUDED.key_facts, entities = EXCLUDED.entities, basis = EXCLUDED.basis, method = EXCLUDED.method""",
                (a["id"], t, ARTICLE_VERSION, _stance(a), article_type(a["headline"]), risk.get("category"), risk.get("level"),
                 Jsonb(a.get("key_facts") or [a["headline"]]), Jsonb(companies)))
            analyses += 1
        for c in companies:
            conn.execute(
                """INSERT INTO news_mentions (article_id, surface, mentions, in_title) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (article_id, surface) DO UPDATE SET mentions = EXCLUDED.mentions, in_title = EXCLUDED.in_title""",
                (a["id"], c.lower(), text.count(squeeze(c)), squeeze(c) in squeeze(a["headline"])))
            mentions += 1
    refresh_events(conn, list(events.values()))
    return {"articles": len(articles), "events": len(events), "analyses": analyses, "mentions": mentions}
