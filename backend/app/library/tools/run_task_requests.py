"""run_task_requests — agent-visible Tool surface over the task_requests workflow.

Shared library: a system binds its meeting store with make_run_task_requests(store).
The requesting system (for submit / list over A2A) is the run's A2A caller, never a model argument.
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import BaseTool, tool

from ...runtime.events import current_run
from ..workflows.meeting_store import MeetingStore
from ..workflows.task_requests import task_requests
from .meeting_access import current_viewer, store_call

TOOL_NAME = "run_task_requests"


def make_run_task_requests(store: MeetingStore) -> BaseTool:
    """Tool instance bound to one system's meeting store."""

    @tool(TOOL_NAME)
    def run_task_requests(
        action: Literal["submit", "list", "accept", "reject"] = "list",
        request_id: str | None = None,
        title: str | None = None,
        detail: str | None = None,
        due: str | None = None,
        suggested_owner: str | None = None,
        source_ref: str | None = None,
        classification: int = 2,
        owner: str | None = None,
        meeting_id: str | None = None,
        note: str | None = None,
        status: Literal["pending", "accepted", "rejected"] | None = None,
    ) -> dict[str, Any]:
        """다른 Agent System(예: 그룹)이 보낸 업무 요청을 접수 · 조회 · 처리한다.
    submit은 다른 시스템의 A2A 요청을 처리할 때만 쓰며, 요청은 승인 대기(pending)로 접수될 뿐 바로 Action Item이 되지 않는다.
    accept(정식 Action Item으로 등록, 담당 · 기한 필요) · reject는 이 시스템 사용자가 직접 요청할 때만, 확정 권한자만 할 수 있다.
    list는 대기 중인 요청(다른 시스템이 물으면 그 시스템이 보낸 요청과 진행 상황)을 보여 준다.

    Args:
        action: submit 접수 · list 조회 · accept 정식 등록 · reject 반려.
        request_id: 요청 id (예: "REQ-2026-10-28-01"). accept · reject 필수, list에서 하나만 볼 때.
        title: submit — 요청하는 업무 (한 문장).
        detail: submit — 배경 · 요구사항 (요청문에 있는 그대로).
        due: submit — 요청한 기한, accept — 확정 기한 (YYYY-MM-DD).
        suggested_owner: submit — 요청한 쪽이 지정한 담당 부서 · 사람.
        source_ref: submit — 요청한 쪽의 기록 id (예: 그룹 회의 Action Item "MTG-2026-10-20-01-A2").
        classification: submit — 등급 1 공개 · 2 사내 · 3 대외비 · 4 극비.
        owner: accept — 이 시스템의 담당 부서 또는 사람 (없으면 suggested_owner).
        meeting_id: accept — 이 업무를 붙일 이 시스템의 회의 id (없으면 접수 기록을 따로 만든다).
        note: accept · reject 의견 (reject는 사유).
        status: list — 상태별 조회.
    """
        viewer = current_viewer()
        run = current_run()
        caller = run.caller if run is not None and run.origin == "a2a" else None
        return store_call(lambda: task_requests(
            store=store, viewer=viewer, caller=caller, action=action, request_id=request_id, title=title, detail=detail,
            due=due, suggested_owner=suggested_owner, source_ref=source_ref, classification=classification, owner=owner,
            meeting_id=meeting_id, note=note, status=status,
        ))

    return run_task_requests
