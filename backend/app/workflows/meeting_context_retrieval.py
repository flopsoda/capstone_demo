"""run_meeting_context_retrieval — Workflow Tool used by the Meeting Agent.

Reads the meeting fixture file, scores each meeting against the request and
returns the decisions and open Action Items of the matching meetings.
The Meeting Agent never touches the fixture directly; this is the boundary.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "meetings.json"

WORKFLOW_NAME = "run_meeting_context_retrieval"

_MIN_TOKEN_LEN = 2
# A meeting needs at least one topic hit (3) or title + statement hits to count.
_MIN_SCORE = 3
# Filler words that appear in almost any request and carry no topic signal.
_STOPWORDS = {
    "관련", "관련해", "관련된", "대한", "대해", "지난", "최근", "회의", "전략회의",
    "회의록", "기록", "결정", "결정한", "결정사항", "기준", "확인", "확인되지", "않은",
    "아직", "찾아줘", "찾아", "조회", "정리", "미완료", "및", "그리고",
    "item", "items", "action", "open", "meeting", "meetings",
}


@lru_cache(maxsize=1)
def load_meetings(path: Path = DATA_PATH) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)["meetings"]


def _terms(query: str, topics: list[str]) -> set[str]:
    """Search terms from the query and topic hints.

    Korean particles are stripped crudely by also adding each token's prefixes
    (뉴로엣지의 → 뉴로엣지, 인수와 → 인수). Single characters are ignored.
    """
    out: set[str] = set()
    for raw in re.findall(r"[0-9A-Za-z가-힣&.\-]+", f"{query} {' '.join(topics)}".lower()):
        if len(raw) < _MIN_TOKEN_LEN or raw in _STOPWORDS:
            continue
        out.add(raw)
        for n in range(_MIN_TOKEN_LEN, len(raw)):
            prefix = raw[:n]
            if prefix not in _STOPWORDS:
                out.add(prefix)
    return out


def _hit(text: str, terms: set[str]) -> bool:
    text = text.lower()
    return any(t in text for t in terms)


def score_meeting(meeting: dict[str, Any], query: str, topics: list[str]) -> int:
    """Keyword overlap score. Topic hits weigh most, then title, then statements."""
    terms = _terms(query, topics)
    score = 0
    for topic in meeting["topics"]:
        t = topic.lower()
        if t in terms or _hit(t, terms):
            score += 3
    if _hit(meeting["title"], terms):
        score += 2
    for d in meeting["decisions"]:
        if _hit(d["statement"], terms):
            score += 1
    return score


def retrieve_meeting_context(
    query: str,
    topics: list[str] | None = None,
    *,
    limit: int = 3,
) -> dict[str, Any]:
    """Pure function behind the tool so it can be unit-tested without an LLM."""
    topics = topics or []
    ranked = sorted(
        ((score_meeting(m, query, topics), m) for m in load_meetings()),
        key=lambda pair: (-pair[0], pair[1]["date"]),
    )
    # Absolute floor plus a relative one: a meeting that scores far below the
    # best match only shares an incidental keyword (e.g. "투자") with the query.
    best = ranked[0][0] if ranked else 0
    threshold = max(_MIN_SCORE, best // 2)
    matches = [m for s, m in ranked if s >= threshold][:limit]
    return {
        "query": query,
        "topics": topics,
        "matched": len(matches),
        "meetings": [
            {
                "id": m["id"],
                "date": m["date"],
                "title": m["title"],
                "topics": m["topics"],
                "decisions": m["decisions"],
                "open_action_items": [
                    {"text": a["text"], "owner": a.get("owner")}
                    for a in m["action_items"]
                    if a.get("status") == "open"
                ],
            }
            for m in matches
        ],
    }


@tool(WORKFLOW_NAME)
def run_meeting_context_retrieval(query: str, topics: list[str] | None = None) -> dict[str, Any]:
    """회의 기록 저장소에서 요청과 관련된 과거 회의를 찾아, 각 회의의 결정사항(발언자별 기준)과
    아직 완료되지 않은 Action Item을 반환한다.

    Args:
        query: 찾고자 하는 내용을 담은 자연어 질의 (예: "뉴로엣지 인수 관련 전략회의 결정사항").
        topics: 관련 주제 키워드 목록 (예: ["NeuroEdge", "M&A", "AI NPU"]).
    """
    return retrieve_meeting_context(query, topics)
