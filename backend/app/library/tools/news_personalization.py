"""News personalization and delivery — agent-visible Tools over the news_delivery workflow
(app/library/workflows/): a user's interests, and their personal briefing sent on request.

Whose profile: the run's principal (by e-mail), never a model argument; runs without a user
(local UIs, tests) use the system's first demo profile. Shared library: a system binds both
to its data folder with the factories below.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from ...runtime.events import current_run
from ..workflows.news_briefing import FixtureSource, GoogleNewsSource
from ..workflows.news_delivery import (
    SubscriberStore,
    deliver,
    format_briefing,
    manage_interests,
    personal_briefing,
    senders_from_env,
)
from ..workflows.meeting_store import MeetingStore
from ..workflows.news_briefing import state_dir
from .run_news_briefing import _llm, internal_context

INTERESTS_TOOL = "manage_news_interests"
PERSONAL_TOOL = "run_personal_news_briefing"


def _who() -> str | None:
    run = current_run()
    return run.principal.email if run and run.principal else None


def make_manage_news_interests(data_dir: Path) -> BaseTool:
    store = SubscriberStore(data_dir)

    @tool(INTERESTS_TOOL)
    def manage_news_interests(action: str = "list", query: str = "", purpose: str = "general",
                              briefing: str = "") -> dict[str, Any]:
        """요청한 사용자의 뉴스 관심 설정(개인화)을 조회하거나 바꾼다.

    Args:
        action: "list"(조회), "add"(관심 주제 추가), "remove"(관심 주제 삭제), "exclude"(이 단어가 든 기사 제외),
            "include"(제외 해제), "set_briefing"(브리핑 주기 변경).
        query: add/remove는 관심 주제(1~3단어), exclude/include는 단어.
        purpose: add일 때 "general"(일반 동향) 또는 "risk"(리스크 점검).
        briefing: set_briefing일 때 "daily" 또는 "weekly".
    """
        return manage_interests(store, _who(), action, query, purpose, briefing)

    return manage_news_interests


def make_run_personal_news_briefing(data_dir: Path, meetings: MeetingStore | None = None) -> BaseTool:
    store = SubscriberStore(data_dir)

    @tool(PERSONAL_TOOL)
    def run_personal_news_briefing(send: bool = False, period: str = "") -> dict[str, Any]:
        """요청한 사용자의 관심 주제 전체로 개인 맞춤 뉴스 브리핑을 만든다(관심 주제별 수집·분석 → 중요도 순 통합,
    제외 단어 기사 제거, 부정 기사 알림). send가 true면 사용자의 채널(텔레그램·이메일)로 발송한다.

    Args:
        send: 텔레그램·이메일로 보낼지. 사용자가 보내 달라고 할 때만 true.
        period: "daily"(1일) 또는 "weekly"(7일). 비우면 사용자 설정.
    """
        sub = store.find(_who())
        if not sub:
            return {"error": "뉴스 구독 정보가 없는 사용자입니다."}
        days = {"daily": 1, "weekly": 7}.get(period)
        b = personal_briefing(sub, sources=[GoogleNewsSource(), FixtureSource(data_dir / "news.json")], llm=_llm(), days=days,
                              context_for=lambda q: internal_context(q, data_dir, meetings),
                              history=state_dir(data_dir) / "news_history.json")
        if send:
            senders, missing = senders_from_env()
            b["delivery"] = deliver(*format_briefing(b), sub.get("channels", ["telegram", "email"]), senders)
            if b["delivery"]["not_configured"]:
                b["delivery"]["setup"] = missing
        return b

    return run_personal_news_briefing
