"""run_decision_review — agent-visible Tool over decision_watch, and the watch job it shares the data with.

Shared library: a system binds its meeting store and its news store with
make_decision_watch(store, articles_path, news=NEWS_STORE). The result has
  .tool    run_decision_review(query, topics, decision_ref) for the Meeting Agent
  .scan()  one pass of the watch job: new articles against the premises of the decisions in force
           (the system's app runs it from its background jobs; the model is the system's chat model)

The watch reads the system's news store — the articles its data file registered and those the feeds brought —
the latest SCAN_LIMIT of the lookback window each pass (a pair already judged is never asked again). Without a
configured news store it reads the data file, as before.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from ..workflows.decision_watch import review_decisions, watch_news
from ..workflows.meeting_store import KST, MeetingStore
from ..workflows.news.store import NewsStore
from .correct_transcript import ask_model
from .meeting_access import current_viewer, store_call

log = logging.getLogger(__name__)


SCAN_LIMIT = 300  # newest articles of the lookback window per pass


def load_articles(path: Path) -> list[dict[str, Any]]:
    """The system fixture's articles (no news store configured)."""
    with path.open(encoding='utf-8') as f:
        return json.load(f)['articles']


def store_articles(news: NewsStore, limit: int = SCAN_LIMIT) -> list[dict[str, Any]]:
    """The news store's latest articles in the article shape the watch reads (id, date, source, headline, summary,
    key_facts, companies): key facts and names from the stored analyses and mentions."""
    from ..workflows.news.pipeline import lookback_days

    since = datetime.now(timezone.utc) - timedelta(days=lookback_days())
    with news.connect() as conn:
        rows = conn.execute(
            """SELECT a.id, a.title, a.summary, a.published_at, o.name AS outlet,
                      coalesce((SELECT jsonb_agg(DISTINCT f) FROM news_article_analysis x, jsonb_array_elements_text(x.key_facts) f
                                WHERE x.article_id = a.id), '[]') AS key_facts,
                      coalesce((SELECT array_agg(DISTINCT n) FROM (
                                    SELECT m.surface AS n FROM news_mentions m WHERE m.article_id = a.id
                                    UNION SELECT e FROM news_article_analysis x, jsonb_array_elements_text(x.entities) e
                                    WHERE x.article_id = a.id) names), '{}') AS companies
               FROM news_articles a JOIN news_outlets o ON o.domain = a.outlet_domain
               WHERE a.published_at >= %s ORDER BY a.published_at DESC, a.id LIMIT %s""", (since, limit)).fetchall()
    return [{"id": r["id"], "date": r["published_at"].astimezone(KST).date().isoformat(), "source": r["outlet"],
             "headline": r["title"], "summary": r["summary"], "key_facts": list(r["key_facts"]), "companies": list(r["companies"])}
            for r in rows]

TOOL_NAME = "run_decision_review"


@dataclass
class DecisionWatch:
    store: MeetingStore
    articles_path: Path
    news: NewsStore | None = None
    tool: BaseTool | None = None

    def articles(self) -> list[dict[str, Any]]:
        if self.news is not None and self.news.configured():
            return store_articles(self.news)
        return load_articles(self.articles_path)

    def scan(self) -> dict[str, int]:
        return watch_news(self.store, self.articles(), ask_model)


def make_decision_watch(store: MeetingStore, articles_path: Path, news: NewsStore | None = None) -> DecisionWatch:
    watch = DecisionWatch(store, articles_path, news)

    @tool(TOOL_NAME)
    def run_decision_review(query: str, topics: list[str] | None = None, decision_ref: str | None = None) -> dict[str, Any]:
        """확정된 회의록의 결정(결정 · 의결 · 기준)이 지금도 유효한지 본다. 질문이나 새 계획과 관련된 결정마다 근거 발언(source),
    회의에서 밝힌 전제 · 조건과 그 상태(holding 유지 · at_risk 흔들림 · broken 깨짐 · met 달성), 회의 뒤에 들어온 뉴스 · 보고가
    그 전제를 흔든 신호(signals: 제목 · 날짜 · 판단 이유), 그 결정을 바꾼 결정(superseded_by)을 돌려준다.
    query에 새 계획 · 제안의 금액이 있으면 결정한 금액과 견주어 넘는 것을 conflicts로, 전제가 흔들렸거나 결정을 넘는 계획이면
    재검토 회의안(re_review: 회의명 · 안건 · 참석 대상 · 이월할 회의 id)을, 이미 잡혀 있으면 그 회의(scheduled_review)를 돌려준다.
    회의를 예약하지는 않는다.

    Args:
        query: 확인할 질문이나 새 계획 · 제안을 받은 그대로 (예: "HBM 증설 계속 가도 돼?", "HBM 라인 2개 총 1조 1천억 원 계획").
        topics: 관련 주제 키워드 (예: ["HBM", "증설"]).
        decision_ref: 결정 하나만 볼 때 그 ref (예: "MTG-2026-11-03-01-D1").
    """
        viewer = current_viewer()
        return store_call(lambda: review_decisions(query, topics, store=store, viewer=viewer, decision=decision_ref))

    watch.tool = run_decision_review
    return watch
