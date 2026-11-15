"""Text helpers shared by every news stage: query terms and headline similarity.

Similarity is the Jaccard overlap of character bigrams: it needs no Korean morphological
analyzer, survives particles and spacing ("삼성전자가" ~ "삼성전자는"), and is explainable.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[0-9A-Za-z가-힣]+")
_NON_WORD = re.compile(r"[^0-9a-z가-힣]")


def terms(query: str) -> list[str]:
    """Search terms of a query: words of two or more characters, lower-cased."""
    return [t.lower() for t in _WORD.findall(query or "") if len(t) >= 2]


def target(query: str) -> str:
    """The normalized subject an analysis is about ("삼성전자  HBM" → "삼성전자 hbm")."""
    return " ".join(terms(query))


def bigrams(text: str) -> set[str]:
    t = _NON_WORD.sub("", text.lower())
    return {t[i : i + 2] for i in range(len(t) - 1)}


def similarity(a: str, b: str, remove: list[str] | tuple[str, ...] = ()) -> float:
    """Bigram Jaccard of two texts after removing `remove` (every result shares the query words)."""
    a, b = a.lower(), b.lower()
    for word in sorted(remove, key=len, reverse=True):
        a, b = a.replace(word.lower(), " "), b.replace(word.lower(), " ")
    x, y = bigrams(a), bigrams(b)
    return len(x & y) / len(x | y) if x and y else 0.0


# Words that say what kind of news is wanted rather than what it is about: when a search with every
# term finds too little, the search is repeated without them ("2차전지 업황" → "2차전지").
GENERIC = {"업황", "동향", "시장", "뉴스", "관련", "최근", "이슈", "전망", "현황", "소식", "분석", "리스크", "산업",
           "정책", "브리핑", "상황", "흐름", "논란", "악재", "호재"}


def core_terms(query: str) -> list[str]:
    return [t for t in terms(query) if t not in GENERIC]
