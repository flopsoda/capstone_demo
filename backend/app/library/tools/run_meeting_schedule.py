"""run_meeting_schedule — agent-visible Tool surface over the meeting_schedule workflow.

Shared library: a system binds its meeting store with make_run_meeting_schedule(store).
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import BaseTool, tool

from ..workflows.meeting_schedule import meeting_schedule
from ..workflows.meeting_store import MeetingStore
from .meeting_access import current_viewer, store_call

TOOL_NAME = "run_meeting_schedule"


def make_run_meeting_schedule(store: MeetingStore) -> BaseTool:
    """Tool instance bound to one system's meeting store."""

    @tool(TOOL_NAME)
    def run_meeting_schedule(
        action: Literal["list", "create", "update", "cancel"] = "list",
        meeting_id: str | None = None,
        title: str | None = None,
        starts_at: str | None = None,
        ends_at: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
        agenda: list[str] | None = None,
        classification: int = 2,
        carry_over_from: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        mine: bool = False,
    ) -> dict[str, Any]:
        """사내 회의 일정을 조회 · 예약 · 변경 · 취소한다 (외부 캘린더와 동기화하지 않는다).
    list는 다가오는 회의(기본 오늘부터 30일)와 안건 · 참석 대상을, create는 새 회의를 예약한다.
    create에 carry_over_from을 주면 그 회의에서 보류된 안건과 해결되지 않은 쟁점을 새 회의 안건으로 이월한다.
    시각은 한국 시간이다.

    Args:
        action: list 조회 · create 예약 · update 변경 · cancel 취소.
        meeting_id: update · cancel할 회의 id.
        title: 회의명 (create 필수).
        starts_at: 시작 시각 "YYYY-MM-DD HH:MM" (create 필수).
        ends_at: 종료 시각 "YYYY-MM-DD HH:MM".
        location: 장소 또는 화상회의 링크.
        attendees: 참석 대상 이름 목록 (update에서는 추가만 한다).
        agenda: 안건 목록 (update에서는 뒤에 추가한다).
        classification: 회의 등급 1 공개 · 2 사내 · 3 대외비 · 4 극비 (create).
        carry_over_from: 안건 · 미결 쟁점을 이월해 올 이전 회의 id (create).
        date_from: list 기간 시작 (YYYY-MM-DD).
        date_to: list 기간 끝 (YYYY-MM-DD).
        mine: list에서 요청자 본인이 참석 대상인 회의만.
    """
        viewer = current_viewer()
        return store_call(lambda: meeting_schedule(
            store=store, viewer=viewer, action=action, meeting_id=meeting_id, title=title, starts_at=starts_at,
            ends_at=ends_at, location=location, attendees=attendees, agenda=agenda, classification=classification,
            carry_over_from=carry_over_from, date_from=date_from, date_to=date_to, mine=mine,
        ))

    return run_meeting_schedule
