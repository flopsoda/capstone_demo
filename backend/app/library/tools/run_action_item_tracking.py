"""run_action_item_tracking — agent-visible Tool surface over the action_items tracking workflow.

Shared library: a system binds its meeting store with make_run_action_item_tracking(store).
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import BaseTool, tool

from ..workflows.action_items import track_action_items
from ..workflows.meeting_store import MeetingStore
from .meeting_access import current_viewer, store_call

TOOL_NAME = "run_action_item_tracking"

Status = Literal["open", "in_progress", "blocked", "done", "cancelled"]


def make_run_action_item_tracking(store: MeetingStore) -> BaseTool:
    """Tool instance bound to one system's meeting store."""

    @tool(TOOL_NAME)
    def run_action_item_tracking(
        query: str | None = None,
        owner: str | None = None,
        status: list[Status] | None = None,
        due_before: str | None = None,
        due_within_days: int | None = None,
        overdue_only: bool = False,
        meeting_id: str | None = None,
        mine: bool = False,
    ) -> dict[str, Any]:
        """여러 회의에서 나온 Action Item의 진행 상황을 조회한다 (조회만 하며 바꾸지 않는다).
    지연 일수가 큰 순, 기한이 가까운 순으로 반환하고, 같은 범위의 상태별 건수(summary)와 기준일(as_of)을 함께 준다.

    Args:
        query: 업무 내용이나 회의명 키워드 (예: "기업가치 산정").
        owner: 담당 부서 또는 사람 이름의 일부 (예: "재무기획팀", "박지훈").
        status: 볼 상태 목록. 기본은 진행 중인 것(open, in_progress, blocked).
        due_before: 이 날짜까지가 기한인 항목만 (YYYY-MM-DD).
        due_within_days: 오늘부터 이 일수 안에 기한이 오는 항목만 (예: "이번 주 마감" → 7, "내일까지" → 1). 날짜 계산이 필요 없다.
        overdue_only: 기한이 지난 미완료 항목만.
        meeting_id: 특정 회의의 항목만 (예: "MTG-2026-09-15").
        mine: 요청자 본인(또는 본인 부서) 담당만.
    """
        viewer = current_viewer()
        return store_call(lambda: track_action_items(
            store=store, viewer=viewer, query=query, owner=owner, status=list(status) if status else None,
            due_before=due_before, due_within_days=due_within_days, overdue_only=overdue_only, meeting_id=meeting_id, mine=mine,
        ))

    return run_action_item_tracking
