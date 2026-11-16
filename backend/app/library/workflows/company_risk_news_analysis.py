"""Company risk news analysis — implementation workflow behind the
run_company_risk_news_analysis Tool (app/library/tools/).

    articles about the company (the news pipeline's stored articles, through risk_articles())
      → topics   : narrow to the risk categories the request asks about
      → issues   : follow-up reports of one matter become one risk (level = its highest)
      → order    : HIGH first; within a level, the newest report first
      → each risk: the lead report, every report's facts, a timeline, a trend, and the
                   points a decision maker should check for that kind of risk

The News Agent never touches the store directly; the tool is the boundary. Articles
without a risk (`"risk": null`) are favourable or neutral news and come back separately
as `positive_signals`, so the agent can weigh them against the risks.

News is public: every caller sees the same articles (no clearance filter here).
Articles come from the news pipeline (news/pipeline.gather) and are turned into this
workflow's article shape by risk_articles(); each persisted event is one matter (`issue`).

This module is not agent-visible and has no LangChain dependency.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import timedelta, timezone
from typing import Any

from .news.labels import CHECK_POINTS, rule_risk, rule_sentiment
from .news.text import similarity
from .news.labels import requested_categories as requested_categories


_KST = timezone(timedelta(hours=9))

_LEVEL_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
_MAX_OUTLETS = 6
# An outlet's tone toward the company: (positive − negative) / its articles about the company.
TONE_THRESHOLD = 0.34
MIN_ARTICLES_FOR_TONE = 2
_MAX_FACTS = 6
_MAX_POSITIVES = 3


def _about_company(article: dict[str, Any], company: str) -> bool:
    """True when any alias of the article's companies appears in `company` (or vice versa).

    `company` may come in as "뉴로엣지", "NeuroEdge" or "뉴로엣지(NeuroEdge)".
    """
    wanted = company.lower().strip()
    if not wanted:
        return False
    for alias in article["companies"]:
        a = alias.lower()
        if a in wanted or wanted in a:
            return True
    return False


def issue_key(article: dict[str, Any]) -> tuple[str, ...]:
    """Articles with the same key report one matter: the article's event (`issue`); an article
    without one is a matter of its own."""
    if article.get("issue"):
        return ("issue", str(article["issue"]))
    return ("article", article["id"])


COVERAGE_ESCALATION = 3  # outlets
SAME_STORY = 0.25  # favourable reports of one story: headline similarity with the company's names removed


def headline_similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    return similarity(a["headline"], b["headline"], sorted({*a["companies"], *b["companies"]}))


def group_issues(articles: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Risk articles → one list per matter (oldest first inside). An article's matter is its
    persistent event (news/cluster.py), so the pipeline's grouping is the risk view's grouping."""
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for a in sorted(articles, key=lambda a: a["date"]):
        groups.setdefault(issue_key(a), []).append(a)
    return list(groups.values())


STANCE_SENTIMENT = {"favorable": "positive", "critical": "negative", "neutral": "neutral", "unknown": "neutral"}


def _sentiment(article: dict[str, Any]) -> str:
    """How the article reports the company: its own stance when it has one (independent of any risk
    it reports); otherwise a risk is negative and no risk is the article's sentiment."""
    if article.get("stance"):
        return STANCE_SENTIMENT.get(article["stance"], "neutral")
    if article.get("risk"):
        return "negative"
    return article.get("sentiment", "positive")


def outlet_tone(articles: list[dict[str, Any]], limit: int = _MAX_OUTLETS) -> list[dict[str, Any]]:
    """Per outlet, how its articles about the company lean (the PR team's view of the press):
    우호 / 비우호 / 중립 (판단 보류 under two articles), the outlets with the most negative articles first."""
    counts: dict[str, dict[str, int]] = {}
    for a in articles:
        c = counts.setdefault(a["source"], {"negative": 0, "positive": 0, "neutral": 0})
        c[_sentiment(a)] += 1
    rows = []
    for outlet, c in counts.items():
        total = sum(c.values())
        score = (c["positive"] - c["negative"]) / total
        if total < MIN_ARTICLES_FOR_TONE:  # one article says nothing about an outlet's stance
            tone = "판단 보류"
        else:
            tone = "우호" if score >= TONE_THRESHOLD else "비우호" if score <= -TONE_THRESHOLD else "중립"
        rows.append({"outlet": outlet, **c, "total": total, "tone": tone})
    rows.sort(key=lambda r: (-r["negative"], -r["total"], r["outlet"]))
    return rows[:limit]


def _unique(items: Iterable[str], limit: int) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen[:limit]


def _trend(reports: list[dict[str, Any]]) -> str:
    """reports: oldest first."""
    if len(reports) == 1:
        return "단일 보도"
    first = _LEVEL_ORDER.get(reports[0]["risk"]["level"], 9)
    last = _LEVEL_ORDER.get(reports[-1]["risk"]["level"], 9)
    if last < first:
        return "확대"
    # Labeled news: a later outlet's milder wording is not an easing of the matter.
    if last > first and not any(r.get("labeled_by") for r in reports):
        return "완화"
    return f"지속 (후속 보도 {len(reports) - 1}건)"


def _source(article: dict[str, Any]) -> dict[str, str]:
    source = {"id": article["id"], "date": article["date"], "name": article["source"]}
    if article.get("url"):  # the original, when the article has a link
        source["url"] = article["url"]
    return source


def _risk_item(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """One risk from the reports of one issue (any order)."""
    chronological = sorted(reports, key=lambda a: a["date"])
    # Lead report: the most severe, and among those the newest.
    level_rank = min(_LEVEL_ORDER.get(a["risk"]["level"], 9) for a in reports)
    candidates = [a for a in chronological if _LEVEL_ORDER.get(a["risk"]["level"], 9) == level_rank]
    lead = candidates[-1]
    others = [a for a in reversed(chronological) if a is not lead]
    category = lead["risk"]["category"]
    outlets = len({a["source"] for a in reports})
    level = lead["risk"]["level"]
    # News labeled by rules sees one article at a time; a story three or more outlets
    # carry matters more than any one headline says, so it goes up one level (and says so).
    escalated = outlets >= COVERAGE_ESCALATION and level != "HIGH" and any(a.get("labeled_by") == "rule" for a in reports)
    if escalated:
        level = "HIGH" if level == "MEDIUM" else "MEDIUM"
    return {
        "category": category,
        "label": lead["risk"]["label"],
        "level": level,
        "escalated_by_coverage": escalated,
        "headline": lead["headline"],
        "summary": lead["summary"],
        "key_facts": _unique([*lead["key_facts"], *(f for a in others for f in a["key_facts"])], _MAX_FACTS),
        "source": _source(lead),
        "report_count": len(reports),
        "outlet_count": outlets,  # how many outlets reported it
        "first_reported": chronological[0]["date"],
        "last_reported": chronological[-1]["date"],
        "trend": _trend(chronological),
        "timeline": [{**_source(a), "headline": a["headline"], "level": a["risk"]["level"]} for a in chronological],
        "check_points": list(CHECK_POINTS.get(category, ())),
    }


def analyze_company_risk_news(
    query: str,
    company: str,
    topics: list[str] | None = None,
    *,
    limit: int = 5,
    articles: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pure function so the workflow can be unit-tested without an LLM.
    Articles: from risk_articles() (or written by a test in the same shape).

    Returns:
        {
          query, company, topics,
          matched: int,                       # number of risks returned
          risks: [ {category, label, level, headline, summary, key_facts,
                    source: {id, date, name},                 # lead report
                    report_count, outlet_count, first_reported, last_reported, trend,
                    timeline: [{id, date, name, headline, level}],   # oldest first
                    check_points: [str]} ... ],      # HIGH first, then newest
          positive_signals: [ {headline, summary, source, outlet_count} ],  # one per story, most covered first
          outlet_tone: [ {outlet, negative, positive, neutral, total, tone} ],  # 우호/비우호/중립/판단 보류, most negative first
        }
    """
    topics = topics or []
    categories = requested_categories(query, topics)
    about = [a for a in articles if _about_company(a, company)]

    risky = [a for a in about if a.get("risk") and (not categories or a["risk"]["category"] in categories)]
    risks = [_risk_item(reports) for reports in group_issues(risky)]
    # Highest level first; within a level, newest lead report first (two stable sorts).
    risks.sort(key=lambda r: r["source"]["date"], reverse=True)
    risks.sort(key=lambda r: _LEVEL_ORDER.get(r["level"], 9))
    risks = risks[:limit]

    # No risk and reported favourably: good news.
    # Many outlets write the same good news (an earnings record): one entry per story, the most covered first.
    stories: list[list[dict[str, Any]]] = []
    for a in sorted((a for a in about if not a.get("risk") and a.get("sentiment", "positive") == "positive"),
                    key=lambda a: a["date"], reverse=True):
        for story in stories:
            if max(headline_similarity(a, m) for m in story) >= SAME_STORY:
                story.append(a)
                break
        else:
            stories.append([a])
    stories.sort(key=lambda st: (len({m["source"] for m in st}), st[0]["date"]), reverse=True)
    return {
        "query": query,
        "company": company,
        "topics": topics,
        "matched": len(risks),
        "risks": risks,
        "outlet_tone": outlet_tone(about),
        "positive_signals": [
            {"headline": st[0]["headline"], "summary": st[0]["summary"], "source": _source(st[0]),
             "outlet_count": len({m["source"] for m in st}), **({"url": st[0]["url"]} if st[0].get("url") else {})}
            for st in stories[:_MAX_POSITIVES]
        ],
    }


# ── Stored articles → the risk view's article shape ─────────────────────────

_SENTENCE = re.compile(r"(?<=[.!?다])\s+")


def _facts(text: str, limit: int = 2) -> list[str]:
    sentences = [s.strip() for s in _SENTENCE.split(text) if len(s.strip()) >= 12]
    return [s[:90] for s in sentences[:limit]]


def risk_articles(rows: list[dict[str, Any]], company: str,
                  analyses: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Stored articles (news/store rows, found by searching for `company`) → this workflow's article
    shape. With `analyses` (news/analysis.py, toward the company) the article's own risk, stance and
    facts are used; without, keyword rules — and an article whose headline does not name the company
    only mentions it in passing (neutral, never a risk or good news of that company). The event an
    article belongs to is its matter (`issue`)."""
    out = []
    for r in rows:
        title, summary, body = r["title"], r.get("summary") or "", r.get("body") or ""
        a = (analyses or {}).get(r["id"])
        if a is not None:
            risk, stance, facts, by = a.get("risk"), a["stance"], a.get("key_facts") or [title], a["method"]
            sentiment = STANCE_SENTIMENT.get(stance, "neutral")
        else:
            named = company.lower() in title.lower()
            text = f"{title} {summary} {body[:1500]}"
            risk = rule_risk(text, title) if named else None
            sentiment = "negative" if risk else (rule_sentiment(text) if named else "neutral")
            if sentiment == "negative" and not risk:
                sentiment = "neutral"
            stance, facts, by = None, _facts(body or summary) or [title], "rule"
        article = {
            "id": r["id"], "date": r["published_at"].astimezone(_KST).date().isoformat(), "source": r["outlet"],
            "headline": title, "summary": summary or body[:200], "companies": [company], "risk": risk,
            "sentiment": sentiment, "key_facts": facts, "url": r.get("url"), "labeled_by": by,
            "issue": str(r["event_id"]) if r.get("event_id") else None, "content_level": r.get("content_level"),
        }
        if stance:
            article["stance"] = stance
        out.append(article)
    return out
