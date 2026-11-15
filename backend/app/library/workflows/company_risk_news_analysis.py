"""Company risk news analysis — implementation workflow behind the
run_company_risk_news_analysis Tool (app/library/tools/).

    articles about the company (the fixture + what news_collection gathered)
      → topics   : narrow to the risk categories the request asks about
      → issues   : follow-up reports of one matter become one risk (level = its highest)
      → order    : HIGH first; within a level, the newest report first
      → each risk: the lead report, every report's facts, a timeline, a trend, and the
                   points a decision maker should check for that kind of risk

The News Agent never touches the fixture directly; the tool is the boundary. Articles
without a risk (`"risk": null`) are favourable or neutral news and come back separately
as `positive_signals`, so the agent can weigh them against the risks.

News is public: every caller sees the same articles (no clearance filter here).
Real news comes in through news_collection.py, which writes the same article shape to a
second store (`collected`); this workflow reads both.

This module is not agent-visible and has no LangChain dependency.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any


_MIN_TOKEN_LEN = 2

# Risk category id → keywords that select it from a natural-language request.
# Matching is case-insensitive substring, so "고객" also covers "주요 고객사".
RISK_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "customer_concentration": ("고객", "매출 의존", "매출의존", "집중도", "customer", "concentration"),
    "legal_dispute": ("법적", "소송", "특허", "분쟁", "legal", "lawsuit", "litigation", "patent"),
    "key_personnel": ("핵심인력", "핵심 인력", "인력", "창업자", "cto", "이탈", "retention", "personnel", "talent"),
    "labor_dispute": ("노사", "노조", "파업", "labor", "strike", "union"),
    "regulatory": ("규제", "제재", "당국", "법안", "regulation", "sanction"),
    "market_reaction": ("주가", "주주", "투자자", "시장 반응", "stock", "shareholder"),
    "operations": ("생산", "공급망", "감산", "수율", "공장", "production", "supply chain"),
}

# What a decision maker should confirm for each kind of risk. The agent turns these into
# the "확인이 필요한 사항" part of its answer; they are questions, not findings.
CHECK_POINTS: dict[str, tuple[str, ...]] = {
    "customer_concentration": (
        "최대 고객의 매출 비중과 공급계약 잔여 기간",
        "고객 다변화 계획과 신규 고객 확보 현황",
        "최대 고객 이탈 시 매출·가치 하락 폭",
    ),
    "legal_dispute": (
        "분쟁 상대와 쟁점, 지금 어느 단계인지 (제기·심리·판결)",
        "예상 배상액과 판매금지(가처분) 등 사업에 미칠 영향",
        "거래 계약의 진술·보장, 손해배상 조항 반영 여부",
    ),
    "key_personnel": (
        "핵심 인력 명단과 잔류 조건(리텐션 보상, 스톡옵션 행사 시점)",
        "통합 후 조직 운영·연구 자율성 보장 방안",
        "핵심 인력 이탈 시 기술 이전·개발 일정 영향",
    ),
    "labor_dispute": (
        "쟁의 일정과 핵심 쟁점",
        "생산·납기에 미치는 영향 범위",
        "노사 협의 대응 계획",
    ),
    "regulatory": (
        "적용되는 규제·법 조항과 시행 시점",
        "사업 계획·주주환원에 미치는 영향 범위",
        "당국·국회 논의 동향과 대응 창구",
    ),
    "market_reaction": (
        "주가 하락·비판의 직접 원인 (실적·주주환원·업황)",
        "주요 주주·외국인 투자자의 반응",
        "IR·주주 소통 계획",
    ),
    "operations": (
        "영향받는 제품·생산 라인과 기간",
        "납기·매출에 미치는 규모",
        "대체 생산·공급처 확보 여부",
    ),
}

_LEVEL_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
_MAX_OUTLETS = 6
# An outlet's tone toward the company: (positive − negative) / its articles about the company.
TONE_THRESHOLD = 0.34
MIN_ARTICLES_FOR_TONE = 2
_MAX_FACTS = 6
_MAX_POSITIVES = 3


def load_articles(path: Path) -> list[dict[str, Any]]:
    """The store's articles; re-read when the file changes (the collector rewrites it while
    the backend runs). A missing store is empty."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return []
    return _load(path, stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=8)
def _load(path: Path, mtime_ns: int, size: int) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)["articles"]


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[0-9A-Za-z가-힣&.\-]+", text.lower()) if len(t) >= _MIN_TOKEN_LEN]


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


def requested_categories(query: str, topics: list[str]) -> set[str]:
    """Risk categories the request mentions. Empty means "no restriction"."""
    text = f"{query} {' '.join(topics)}".lower()
    return {cat for cat, keys in RISK_CATEGORY_KEYWORDS.items() if any(k in text for k in keys)}


def issue_key(article: dict[str, Any]) -> tuple[str, ...]:
    """Articles with the same key report one matter. A fixture may name the issue
    explicitly (`"issue"`); otherwise reports of the same risk category about exactly the
    same set of companies (the subject and, e.g., the plaintiff or customer) are one matter."""
    if article.get("issue"):
        return ("issue", str(article["issue"]))
    return ("auto", article["risk"]["category"], *sorted(a.lower() for a in article["companies"]))


# Collected (real) news: two reports are one story only when their headlines, minus the company's
# own name, share enough. Same category + same company is not enough there (a company can face a
# shareholder suit and a patent suit at once). Fixture articles keep the plain key above.
SAME_STORY = 0.25
# Market reaction is one theme told in many words ("주가 급락", "외신 비판", "개미들 비명"):
# collected reports of it within a few days are one matter, whatever the headlines say.
THEME_CATEGORIES = {"market_reaction": 3}
COVERAGE_ESCALATION = 3  # outlets
_NON_WORD = re.compile(r"[^0-9A-Za-z가-힣]")


def _bigrams(text: str) -> set[str]:
    t = _NON_WORD.sub("", text.lower())
    return {t[i : i + 2] for i in range(len(t) - 1)}


def headline_similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    names = sorted({*a["companies"], *b["companies"]}, key=len, reverse=True)

    def strip(h: str) -> str:
        for n in names:
            h = h.replace(n, " ")
        return h

    x, y = _bigrams(strip(a["headline"])), _bigrams(strip(b["headline"]))
    return len(x & y) / len(x | y) if x and y else 0.0


def _same_matter(a: dict[str, Any], members: list[dict[str, Any]]) -> bool:
    window = THEME_CATEGORIES.get(a["risk"]["category"])
    if window is not None:
        latest = max(date.fromisoformat(m["date"]) for m in members)
        return (date.fromisoformat(a["date"]) - latest).days <= window
    return max(headline_similarity(a, m) for m in members) >= SAME_STORY


def group_issues(articles: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Risk articles → one list per matter (oldest first inside)."""
    groups: list[tuple[tuple[str, ...], list[dict[str, Any]]]] = []
    for a in sorted(articles, key=lambda a: a["date"]):
        key = issue_key(a)
        for g_key, members in groups:
            if g_key != key:
                continue
            if a.get("labeled_by") and not a.get("issue") and not _same_matter(a, members):
                continue
            members.append(a)
            break
        else:
            groups.append((key, [a]))
    return [members for _, members in groups]


def _sentiment(article: dict[str, Any]) -> str:
    """negative (a risk) | positive | neutral. Fixture articles without a risk are good news."""
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
    # Collected news: a later outlet's milder wording is not an easing of the matter.
    if last > first and not any(r.get("labeled_by") for r in reports):
        return "완화"
    return f"지속 (후속 보도 {len(reports) - 1}건)"


def _source(article: dict[str, Any]) -> dict[str, str]:
    source = {"id": article["id"], "date": article["date"], "name": article["source"]}
    if article.get("url"):  # collected (real) articles link to the original
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
    # Collected news labeled by rules sees one article at a time; a story three or more outlets
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
    path: Path,
    collected: Path | None = None,
) -> dict[str, Any]:
    """Pure function so the workflow can be unit-tested without an LLM.
    `path` is the article store this system owns (bound by the system's Tool), `collected`
    the store news_collection fills (optional; missing = nothing collected yet).

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
    articles = [*load_articles(path), *(load_articles(collected) if collected else [])]
    about = [a for a in articles if _about_company(a, company)]

    risky = [a for a in about if a.get("risk") and (not categories or a["risk"]["category"] in categories)]
    risks = [_risk_item(reports) for reports in group_issues(risky)]
    # Highest level first; within a level, newest lead report first (two stable sorts).
    risks.sort(key=lambda r: r["source"]["date"], reverse=True)
    risks.sort(key=lambda r: _LEVEL_ORDER.get(r["level"], 9))
    risks = risks[:limit]

    # No risk and not marked neutral: fixture articles with "risk": null are good news; collected ones say so.
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
