"""update_action_item — agent-visible Tool surface over the action_items update workflow.

Shared library: a system binds its meeting store with make_update_action_item(store).
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import BaseTool, tool

from ..workflows.action_items import update_action_item as update_item
from ..workflows.meeting_store import MeetingStore
from .meeting_access import current_viewer, store_call

TOOL_NAME = "update_action_item"


def make_update_action_item(store: MeetingStore) -> BaseTool:
    """Tool instance bound to one system's meeting store."""

    @tool(TOOL_NAME)
    def update_action_item(
        item_id: str,
        status: Literal["open", "in_progress", "blocked", "done", "cancelled"] | None = None,
        note: str | None = None,
        due: str | None = None,
        owner: str | None = None,
        remind: bool = False,
    ) -> dict[str, Any]:
        """Action Item 하나의 진행 상황을 갱신한다. 변경은 누가 언제 보고했는지와 함께 이력으로 남는다.
    사용자가 명시적으로 말한 변경만 반영한다. id를 모르면 결과의 candidates에서 고르도록 사용자에게 묻는다.

    Args:
        item_id: Action Item id (예: "MTG-2026-09-15-A1").
        status: 새 상태 — open 미착수 · in_progress 진행 중 · blocked 막힘 · done 완료 · cancelled 취소.
        note: 진행 메모 (예: "DCF 1차 산정 완료, 민감도 분석 중").
        due: 새 기한 YYYY-MM-DD.
        owner: 새 담당 부서 또는 사람.
        remind: true면 담당자에게 지금 진행 확인 요청(독촉) 메일을 보낸다. 사용자가 독촉 · 재촉 · 알림을 요청했을 때만 쓴다.
            확정된 회의록의 열린 할 일, 담당이 사람일 때만 보내며 같은 할 일은 하루 한 번. 사용자가 덧붙인 말은 note로 넘긴다.
            다른 Agent System(그룹 등)이 보낸 요청으로는 remind만 할 수 있다 (status · due · owner는 바꿀 수 없다).
    """
        viewer = current_viewer()
        return store_call(lambda: update_item(
            store=store, viewer=viewer, item_id=item_id, status=status, note=note, due=due, owner=owner, remind=remind,
        ))

    return update_action_item
