"""News Briefing — the News Agent's basic workflow (the original design, one run per request).

    query (any: a company, an industry, a market, a regulation, a person)
      1. collect     : Source Adapters fetch articles for the query (Google News RSS; the demo
                       scenario's fictional articles are one more adapter)
      2. normalize   : one article shape — id, title, summary, url, outlet, date
      3. filter      : duplicates out, reports of one story grouped into a topic, every topic
                       scored for importance (coverage, recency, how squarely it is about the query)
      4. analyze     : per topic a summary, key facts and sentiment; across topics the trend and the
                       So-What for the request's purpose — an LLM when one is configured, rules otherwise
      5. respond     : a structured result the agent turns into its briefing

`purpose` selects optional analysis steps on top of the general ones. "general" is the default;
"risk" adds a risk category, level and check points per topic (the M&A scenario uses it).
Nothing is stored between runs: storing articles in a database / a shared knowledge pool is a
later, separate feature.

Not agent-visible, no LangChain dependency: the Tool passes `llm` as (system, user) -> text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from .company_risk_news_analysis import CHECK_POINTS
from .news_collection import (
    ARTICLE_CUES,
    CATEGORY_LABELS,
    HIGH_CUES,
    KST,
    LOW_CUES,
    OPINION_CUES,
    POSITIVE_CUES,
    Fetch,
    fetch_google_news,
    http_fetch,
    parse_json_array,
)

LLM = Callable[[str, str], str]
Log = Callable[[str, dict], None]

PURPOSES = ("general", "risk")
SAME_STORY = 0.25  # headline similarity (query words removed) for two reports of one story
TONE_MIN_ARTICLES = 2
TONE_THRESHOLD = 0.34


# ── 1–2. Collect and normalize ──────────────────────────────────────────────

@dataclass
class Article:
    id: str
    title: str
    summary: str
    url: str
    outlet: str
    date: str  # ISO date
    source: str  # which adapter found it
    hint: dict[str, Any] | None = None  # labels a source already carries (the demo fixture's risk)


class Source(Protocol):
    name: str

    def search(self, query: str, since: date) -> list[Article]: ...


class GoogleNewsSource:
    name = "google_news"

    def __init__(self, fetch: Fetch = http_fetch, limit: int = 40):
        self.fetch, self.limit = fetch, limit

    def search(self, query: str, since: date) -> list[Article]:
        days = max(1, (datetime.now(KST).date() - since).days)
        words = query.split()
        # One word: that name exactly. Several: every word, in any order.
        raws = fetch_google_news(query, [], days, fetch=self.fetch, limit=self.limit, exact=len(words) == 1)
        if not raws and len(words) > 2:  # a long, model-made query ("뉴로엣지 리스크 논란 분쟁") finds little
            raws = fetch_google_news(" ".join(words[:2]), [], days, fetch=self.fetch, limit=self.limit, exact=False)
        return [Article(r.id, r.title, r.summary, r.url, r.publisher, r.published.isoformat(), self.name)
                for r in raws if r.published >= since]


class FixtureSource:
    """The demo scenario's fictional articles (data/news.json) as one more source. An article
    matches when the query names one of its companies, or when every query word appears in it."""

    name = "demo_fixture"

    def __init__(self, path: Path):
        self.path = path

    def search(self, query: str, since: date) -> list[Article]:
        if not self.path.exists():
            return []
        terms, q = _terms(query), query.lower()
        out = []
        for a in json.loads(self.path.read_text(encoding="utf-8"))["articles"]:
            text = " ".join([a["headline"], a["summary"], *a.get("companies", [])]).lower()
            named = any(c.lower() in q for c in a.get("companies", []))
            if named or (terms and all(t in text for t in terms)):
                out.append(Article(a["id"], a["headline"], a["summary"], a.get("url", ""), a["source"], a["date"],
                                   self.name, {"risk": a.get("risk"), "key_facts": a.get("key_facts", []),
                                               "companies": a.get("companies", [])}))
        return out  # fictional articles keep their dates: the scenario is set in a fixed time


# ── 3. Filter ───────────────────────────────────────────────────────────────

_WORD = re.compile(r"[0-9A-Za-z가-힣]+")


def _terms(query: str) -> list[str]:
    return [t.lower() for t in _WORD.findall(query) if len(t) >= 2]


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"[^0-9a-z가-힣]", "", text.lower())
    return {t[i : i + 2] for i in range(len(t) - 1)}


def _similarity(a: str, b: str, query_terms: list[str]) -> float:
    for term in sorted(query_terms, key=len, reverse=True):  # every result shares the query words
        a, b = a.lower().replace(term, " "), b.lower().replace(term, " ")
    x, y = _bigrams(a), _bigrams(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def dedupe(articles: list[Article]) -> tuple[list[Article], int]:
    """Same link, or the same headline from the same outlet: one copy. Other outlets' reports of the
    same story stay — they are coverage, counted when topics are formed."""
    kept: list[Article] = []
    for a in sorted(articles, key=lambda a: a.date):
        if any((a.url and a.url == k.url) or (a.outlet == k.outlet and _similarity(a.title, k.title, []) >= 0.6)
               for k in kept):
            continue
        kept.append(a)
    return kept, len(articles) - len(kept)


def _same_labeled_matter(a: Article, b: Article) -> bool:
    """Articles a source already labeled (the demo fixture): the same risk about the same companies
    is one matter even when the headlines differ (a lawsuit, then an injunction in it)."""
    ha, hb = a.hint or {}, b.hint or {}
    ra, rb = ha.get("risk"), hb.get("risk")
    return bool(ra and rb and ra["category"] == rb["category"] and set(ha.get("companies", [])) == set(hb.get("companies", [])))


def group_topics(articles: list[Article], query: str) -> list[list[Article]]:
    terms = _terms(query)
    topics: list[list[Article]] = []
    for a in sorted(articles, key=lambda a: a.date):
        for t in topics:
            if any(_same_labeled_matter(a, m) for m in t) or max(_similarity(a.title, m.title, terms) for m in t) >= SAME_STORY:
                t.append(a)
                break
        else:
            topics.append([a])
    return topics


def importance(topic: list[Article], query: str, today: date) -> float:
    """0–100 without reading the articles: how widely it was reported (outlets), how recent it is,
    and whether the query is the subject of the headline rather than a passing mention."""
    outlets = len({a.outlet for a in topic})
    newest = max(date.fromisoformat(a.date) for a in topic)
    age = max(0, (today - newest).days)
    terms = _terms(query)
    # Share of the query's words in each headline: "삼성전자 노조" scores a headline about Samsung's
    # union above one that names Samsung in passing.
    in_title = sum(sum(t in a.title.lower() for t in terms) / len(terms) for a in topic) / len(topic) if terms else 0
    score = min(outlets, 5) * 10 + max(0, 30 - age * 4) + in_title * 20
    return round(min(100.0, score), 1)


def adjusted_importance(t: dict[str, Any]) -> float:
    """The filter's score, then what the content says: the model's own rating and, for
    purpose="risk", a high risk. A positive/neutral topic is not pushed up by its risk label."""
    score = t["importance"] + {"high": 15, "low": -10}.get(t.get("importance_llm", ""), 0)
    risk = t.get("risk")
    if risk and risk["level"] == "HIGH":
        score += 15
    return round(max(0.0, min(100.0, score)), 1)


# ── 4. Analyze ──────────────────────────────────────────────────────────────

def _rule_sentiment(text: str) -> str:
    if any(c in text for cues in ARTICLE_CUES.values() for c in cues):
        return "negative"
    return "positive" if any(c in text for c in POSITIVE_CUES) else "neutral"


def _rule_risk(text: str, title: str) -> dict[str, Any] | None:
    hits = {cat: sum(c in text for c in cues) for cat, cues in ARTICLE_CUES.items()}
    category = max(hits, key=hits.get)
    if not hits[category]:
        return None
    if any(c in title for c in OPINION_CUES):
        level = "LOW"
    else:
        level = "HIGH" if any(c in text for c in HIGH_CUES) else "LOW" if any(c in text for c in LOW_CUES) else "MEDIUM"
    return {"category": category, "label": CATEGORY_LABELS[category], "level": level}


ANALYZE_SYSTEM = """너는 경영진에게 뉴스를 보고하는 분석가다. 주제(topic)별로 묶인 기사 목록을 읽고 JSON 객체 하나만 출력한다. 설명 문장은 쓰지 않는다.
{{"topics": [{{"id": 주제 id, "summary": 무슨 일인지 1~2문장, "key_facts": 기사에 적힌 사실 1~3개,
              "sentiment": "positive" | "negative" | "neutral" (요청 대상에게 좋은 소식/나쁜 소식/중립),
              "importance": "high" | "medium" | "low"{risk_fields}}}],
 "trend": 전체 기사에서 보이는 흐름 2~3문장,
 "so_what": 요청 목적({purpose}) 관점에서 경영진이 알아야 할 시사점 2~3문장}}
- 기사에 없는 사실이나 수치를 만들지 않는다. 추측성 보도와 칼럼은 그렇다고 밝힌다."""

RISK_FIELDS = (',\n              "risk": 리스크가 아니면 null, 리스크면 {"category": '
               + " | ".join(f'"{k}"' for k in CATEGORY_LABELS) + ', "level": "HIGH" | "MEDIUM" | "LOW"}')


def _parse_object(text: str) -> dict | None:
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def analyze(topics: list[dict[str, Any]], query: str, purpose: str, llm: LLM | None) -> tuple[str, str, str]:
    """Fills summary, key_facts, sentiment (and risk for purpose="risk") on each topic in place.
    Returns (trend, so_what, method)."""
    for t in topics:  # the rule pass first: every field has a value even if the model fails
        lead = t["articles"][0]
        text = " ".join(a["title"] + " " + a["summary"] for a in t["articles"])
        hint = t.pop("_hint", None) or {}
        t["summary"] = lead["title"]
        t["key_facts"] = hint.get("key_facts") or list(dict.fromkeys(a["title"] for a in t["articles"]))[:3]
        t["sentiment"] = "negative" if hint.get("risk") else _rule_sentiment(text)
        if purpose == "risk":
            risk = hint.get("risk") or _rule_risk(text, lead["title"])
            t["risk"] = risk
            t["check_points"] = list(CHECK_POINTS.get(risk["category"], ())) if risk else []
    if not (llm and topics):
        return "", "", "rule"

    payload = [{"id": t["id"], "articles": [{"title": a["title"], "summary": a["summary"][:300], "outlet": a["outlet"],
                                             "date": a["date"]} for a in t["articles"][:5]]} for t in topics]
    system = ANALYZE_SYSTEM.format(purpose="리스크 점검" if purpose == "risk" else "일반 동향 파악",
                                   risk_fields=RISK_FIELDS if purpose == "risk" else "")
    try:
        answer = _parse_object(llm(system, f"요청: {query}\n\n주제별 기사:\n{json.dumps(payload, ensure_ascii=False)}"))
    except Exception:  # network, quota, model errors: the rule pass stands
        answer = None
    if not answer:
        return "", "", "rule"
    by_id = {t["id"]: t for t in topics}
    for row in answer.get("topics") or []:
        t = by_id.get(row.get("id")) if isinstance(row, dict) else None
        if not t:
            continue
        if isinstance(row.get("summary"), str) and row["summary"].strip():
            t["summary"] = row["summary"].strip()
        facts = [str(f).strip() for f in row.get("key_facts") or [] if str(f).strip()]
        if facts:
            t["key_facts"] = facts[:3]
        if row.get("sentiment") in ("positive", "negative", "neutral"):
            t["sentiment"] = row["sentiment"]
        if row.get("importance") in ("high", "medium", "low"):
            t["importance_llm"] = row["importance"]
        if purpose == "risk" and "risk" in row:
            r = row["risk"]
            if r is None:
                t["risk"], t["check_points"] = None, []
            elif isinstance(r, dict) and r.get("category") in CATEGORY_LABELS and r.get("level") in ("HIGH", "MEDIUM", "LOW"):
                t["risk"] = {"category": r["category"], "label": CATEGORY_LABELS[r["category"]], "level": r["level"]}
                t["check_points"] = list(CHECK_POINTS.get(r["category"], ()))
    trend = answer.get("trend") if isinstance(answer.get("trend"), str) else ""
    so_what = answer.get("so_what") if isinstance(answer.get("so_what"), str) else ""
    return trend.strip(), so_what.strip(), "llm"


def outlet_tone(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, int]] = {}
    for t in topics:
        for a in t["articles"]:
            counts.setdefault(a["outlet"], {"negative": 0, "positive": 0, "neutral": 0})[t["sentiment"]] += 1
    rows = []
    for outlet, c in counts.items():
        total = sum(c.values())
        score = (c["positive"] - c["negative"]) / total
        tone = ("판단 보류" if total < TONE_MIN_ARTICLES else
                "우호" if score >= TONE_THRESHOLD else "비우호" if score <= -TONE_THRESHOLD else "중립")
        rows.append({"outlet": outlet, **c, "total": total, "tone": tone})
    rows.sort(key=lambda r: (-r["negative"], -r["total"], r["outlet"]))
    return rows


# ── The workflow ────────────────────────────────────────────────────────────

def run_news_briefing(
    query: str,
    *,
    purpose: str = "general",
    days: int = 7,
    limit: int = 8,
    sources: list[Source],
    llm: LLM | None = None,
    today: date | None = None,
    log: Log | None = None,
) -> dict[str, Any]:
    log = log or (lambda stage, detail: None)
    query = (query or "").strip()
    purpose = purpose if purpose in PURPOSES else "general"
    today = today or datetime.now(KST).date()
    if not query:
        return {"query": "", "error": "검색할 주제가 없습니다.", "topics": []}
    since = today - timedelta(days=days)

    # 1–2. collect + normalize
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

    # 3. filter
    unique, duplicates = dedupe(articles)
    # Recency counts from the newest collected article: live news ends today, the fictional
    # scenario ends on its own last day, and either way the latest story scores as new.
    as_of = min(today, max((date.fromisoformat(a.date) for a in unique), default=today))
    groups = group_topics(unique, query)
    topics = []
    for g in groups:
        g.sort(key=lambda a: a.date, reverse=True)
        topics.append({
            "importance": importance(g, query, as_of),
            "article_count": len(g),
            "outlet_count": len({a.outlet for a in g}),
            "first_reported": min(a.date for a in g),
            "last_reported": max(a.date for a in g),
            "articles": [{"id": a.id, "title": a.title, "summary": a.summary, "outlet": a.outlet, "date": a.date,
                          **({"url": a.url} if a.url else {})} for a in g],
            "_hint": next((a.hint for a in g if a.hint), None),
        })
    topics.sort(key=lambda t: t["last_reported"], reverse=True)  # newest first among equals
    topics.sort(key=lambda t: t["importance"], reverse=True)
    topics = topics[:limit]
    for n, t in enumerate(topics, 1):
        t["id"] = f"topic-{n}"
    log("filter", {"articles": len(articles), "duplicates": duplicates, "topics": len(groups), "kept": len(topics)})

    # 4. analyze
    trend, so_what, method = analyze(topics, query, purpose, llm)
    for t in topics:
        t["importance"] = adjusted_importance(t)
    topics.sort(key=lambda t: t["importance"], reverse=True)  # ids stay: the LLM answered by id
    log("analyze", {"method": method})

    # 5. respond
    result = {
        "query": query,
        "purpose": purpose,
        "period": {"from": since.isoformat(), "to": today.isoformat()},
        "collected": len(articles),
        "duplicates_removed": duplicates,
        "topic_count": len(groups),
        "analysis": method,
        "trend": trend,
        "so_what": so_what,
        "topics": topics,
        "negative_alerts": [t["id"] for t in topics if t["sentiment"] == "negative" and t["importance"] >= 50],
        "outlet_tone": outlet_tone(topics),
        "failed_sources": failed,
    }
    log("respond", {"topics": len(topics)})
    return result


def topic_line(t: dict[str, Any]) -> str:
    """One line per topic for a plain-text briefing (the demo script uses it)."""
    mood = {"negative": "부정", "positive": "긍정", "neutral": "중립"}[t["sentiment"]]
    risk = f" · {t['risk']['label']} {t['risk']['level']}" if t.get("risk") else ""
    return f"[중요도 {t['importance']:.0f} · {mood}{risk} · 기사 {t['article_count']}건/매체 {t['outlet_count']}곳] {t['summary']}"
