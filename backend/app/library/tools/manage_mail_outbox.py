"""manage_mail_outbox — agent-visible Tool surface over the outbox review workflow.

Shared library: a system binds its meeting store with make_manage_mail_outbox(store).
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import BaseTool, tool

from ..workflows.meeting_store import MeetingStore
from ..workflows.outbox import manage_outbox
from .meeting_access import current_viewer, store_call

TOOL_NAME = "manage_mail_outbox"


def make_manage_mail_outbox(store: MeetingStore) -> BaseTool:
    """Tool instance bound to one system's meeting store."""

    @tool(TOOL_NAME)
    def manage_mail_outbox(
        action: Literal["list", "resend", "reject", "set_address"] = "list",
        mail_ids: list[str] | None = None,
        meeting_id: str | None = None,
        email: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """회의 메일 발송함을 조회한다. 회의록 확정 · 정정, 회의 예약 · 변경 · 취소, 기한 알림 메일은 Agent가 받는 사람의 주소를
    스스로 찾아(로그인 등록 이메일 → 임직원 · 외부 연락처) 승인 없이 바로 보낸다. 어디에서도 주소를 찾지 못한 메일은 needs_address로
    남아 매일 아침 다시 찾는다. 다시 보내기 · 취소 · 주소 정정은 그 회의의 의장 · 간사 또는 확정 권한 그룹만 할 수 있다.

    Args:
        action: list 보낼 메일 · 주소를 못 찾은 메일 · 실패 메일 · 최근 7일 보낸 메일 조회 (본문 포함)
            · resend 실패한 메일 다시 보내기 · reject 아직 안 보낸 메일 취소 · set_address 받는 주소 정정 (정정하면 바로 다시 보냄).
        mail_ids: 대상 메일 id 목록 (예: ["MAIL-12", "MAIL-13"]). resend · reject는 meeting_id로 그 회의의 메일 전체를 지정해도 된다.
        meeting_id: 한 회의의 메일만 볼 때 · 처리할 때 그 회의 id.
        email: set_address의 받는 주소 (사용자가 알려 준 그대로).
        note: reject 사유.
    """
        viewer = current_viewer()
        return store_call(lambda: manage_outbox(
            store=store, viewer=viewer, action=action, mail_ids=mail_ids, meeting_id=meeting_id, email=email, note=note,
        ))

    return manage_mail_outbox
