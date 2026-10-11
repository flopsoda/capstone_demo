"""Company risk news analysis — implementation workflow behind the
run_company_risk_news_analysis Tool (app/capabilities/tools/).

Reads the news fixture file, keeps the articles about the target company,
narrows them to the risk categories the request asks about, and returns one
structured risk item per article. The News Agent never touches the fixture
directly; the tool is the boundary.

This module is not agent-visible and has no LangChain dependency. A real
implementation (news API, web search, RAG) replaces the body of
analyze_company_risk_news() while keeping its input/output contract.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "news.json"

_MIN_TOKEN_LEN = 2

# Risk category id → keywords that select it from a natural-language request.
# Matching is case-insensitive substring, so "고객" also covers "주요 고객사".
RISK_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "customer_concentration": ("고객", "매출 의존", "매출의존", "집중도", "customer", "concentration"),
    "legal_dispute": ("법적", "소송", "특허", "분쟁", "legal", "lawsuit", "litigation", "patent"),
    "key_personnel": ("핵심인력", "핵심 인력", "인력", "창업자", "cto", "이탈", "retention", "personnel", "talent"),
    "labor_dispute": ("노사", "노조", "파업", "labor", "strike", "union"),
}

_LEVEL_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


@lru_cache(maxsize=1)
def load_articles(path: Path = DATA_PATH) -> list[dict[str, Any]]:
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


def analyze_company_risk_news(
    query: str,
    company: str,
    topics: list[str] | None = None,
    *,
    limit: int = 5,
) -> dict[str, Any]:
    """Pure function so the workflow can be unit-tested without an LLM.

    Returns:
        {
          query, company, topics,
          matched: int,
          risks: [ {category, label, level, headline, summary, key_facts,
                    source: {id, date, name}} ... ]   # HIGH first, then newest
        }
    """
    topics = topics or []
    categories = requested_categories(query, topics)
    about = [a for a in load_articles() if _about_company(a, company)]
    selected = [a for a in about if not categories or a["risk"]["category"] in categories]
    # Highest level first; within a level, newest first (two stable sorts).
    selected.sort(key=lambda a: a["date"], reverse=True)
    selected.sort(key=lambda a: _LEVEL_ORDER.get(a["risk"]["level"], 9))
    selected = selected[:limit]
    return {
        "query": query,
        "company": company,
        "topics": topics,
        "matched": len(selected),
        "risks": [
            {
                "category": a["risk"]["category"],
                "label": a["risk"]["label"],
                "level": a["risk"]["level"],
                "headline": a["headline"],
                "summary": a["summary"],
                "key_facts": list(a["key_facts"]),
                "source": {"id": a["id"], "date": a["date"], "name": a["source"]},
            }
            for a in selected
        ],
    }
