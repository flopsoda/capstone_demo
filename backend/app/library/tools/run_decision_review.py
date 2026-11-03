"""run_decision_review — agent-visible Tool over decision_watch, and the watch job it shares the data with.

Shared library: a system binds its meeting store and its article store with
make_decision_watch(store, articles_path). The result has
  .tool    run_decision_review(query, topics, decision_ref) for the Meeting Agent
  .scan()  one pass of the watch job: new articles against the premises of the decisions in force
           (the system's app runs it from its background jobs; the model is the system's chat model)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from ..workflows.company_risk_news_analysis import load_articles
from ..workflows.decision_watch import review_decisions, watch_news
from ..workflows.meeting_store import MeetingStore
from .correct_transcript import ask_model
from .meeting_access import current_viewer, store_call

log = logging.getLogger(__name__)

TOOL_NAME = "run_decision_review"


@dataclass
class DecisionWatch:
    store: MeetingStore
    articles_path: Path
    tool: BaseTool | None = None

    def scan(self) -> dict[str, int]:
        return watch_news(self.store, load_articles(self.articles_path), ask_model)


def make_decision_watch(store: MeetingStore, articles_path: Path) -> DecisionWatch:
    watch = DecisionWatch(store, articles_path)

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
