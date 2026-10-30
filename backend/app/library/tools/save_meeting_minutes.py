"""save_meeting_minutes — agent-visible Tool surface over the meeting_minutes workflow.

Shared library: a system binds its meeting store with make_save_meeting_minutes(store).
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from ..workflows.meeting_minutes import save_minutes
from ..workflows.meeting_store import MeetingStore
from .meeting_access import current_viewer, plain, store_call

TOOL_NAME = "save_meeting_minutes"


class Attendee(BaseModel):
    name: str = Field(description="이름 (예: 박지훈). transcript 라벨 '박지훈 (CFO)' 형태도 된다")
    title: str | None = Field(default=None, description="직책 (예: CFO)")
    role: Literal["chair", "secretary", "required", "optional", "observer"] = Field(
        default="required", description="chair 의장 · secretary 간사 · required 참석 대상 · optional 선택 참석 · observer 배석"
    )
    attended: bool = Field(default=True, description="실제 출석 여부 (불참자는 false)")


class Dissent(BaseModel):
    name: str = Field(description="반대 · 기권 · 회피한 사람")
    position: Literal["against", "abstain", "recused"] = "against"
    reason: str | None = Field(default=None, description="그 이유 (회의에서 밝힌 그대로)")


class Decision(BaseModel):
    kind: Literal["decision", "resolution", "criterion", "opinion", "issue"] = Field(
        default="decision",
        description="decision 합의된 결정 · resolution 표결로 의결 · criterion 이후 판단 기준 · opinion 주요 발언 · issue 결론 내지 못한 쟁점/질문",
    )
    statement: str = Field(description="결정 · 기준 · 발언 · 쟁점의 내용")
    speaker: str | None = Field(default=None, description="발언자 (criterion · opinion은 필수). transcript에 드러난 경우만")
    rationale: str | None = Field(default=None, description="그렇게 정한 근거가 회의에서 언급됐으면")
    outcome: Literal["approved", "amended", "rejected", "deferred"] | None = Field(
        default=None, description="resolution만: 원안가결 · 수정가결 · 부결 · 보류"
    )
    votes_for: int | None = Field(default=None, description="resolution만: 찬성 수")
    votes_against: int | None = Field(default=None, description="resolution만: 반대 수")
    votes_abstain: int | None = Field(default=None, description="resolution만: 기권 수")
    dissents: list[Dissent] = Field(default_factory=list, description="반대 · 기권한 사람과 이유 (기명일 때)")
    supersedes: str | None = Field(default=None, description="이 결정이 바꾸는 이전 결정의 ref (예: MTG-2026-09-15-D1)")
    resolves: str | None = Field(default=None, description="이 결정이 해결한 이전 쟁점의 ref")
    source_time: str | None = Field(default=None, description="근거 발언의 transcript 시각 (예: '03:15'). transcript가 연결된 경우만")


class ActionItem(BaseModel):
    text: str = Field(description="해야 할 일")
    owner: str | None = Field(default=None, description="담당 부서 또는 사람 (예: 재무기획팀, 박지훈). 회의에서 정해진 경우만")
    due: str | None = Field(default=None, description="기한 YYYY-MM-DD. 회의에서 정해진 경우만")
    id: str | None = Field(default=None, description="기존 항목을 고칠 때 그 id (예: MTG-2026-10-25-01-A2)")
    source_time: str | None = Field(default=None, description="이 업무가 정해진 발언의 transcript 시각 (예: '12:40'). transcript가 연결된 경우만")


class FlagReview(BaseModel):
    id: str = Field(description="Compliance 검토 항목 id (예: CF-12)")
    status: Literal["cleared", "acknowledged"] = Field(
        description="cleared 문제없음(오탐) · acknowledged 문제를 확인하고 조치함 (어떤 조치인지 note 필수)"
    )
    note: str | None = Field(default=None, description="검토 의견 · 조치 내용 (사용자가 말한 그대로)")


def make_save_meeting_minutes(store: MeetingStore) -> BaseTool:
    """Tool instance bound to one system's meeting store."""

    @tool(TOOL_NAME)
    def save_meeting_minutes(
        title: str,
        date: str,
        summary: str,
        decisions: list[Decision] | None = None,
        action_items: list[ActionItem] | None = None,
        attendees: list[Attendee] | None = None,
        topics: list[str] | None = None,
        body: str | None = None,
        classification: int = 2,
        location: str | None = None,
        meeting_id: str | None = None,
        finalize: bool = False,
        correction_reason: str | None = None,
        transcript_id: str | None = None,
        flag_reviews: list[FlagReview] | None = None,
    ) -> dict[str, Any]:
        """회의록을 회의 기록 저장소에 저장한다. 처음에는 초안(draft)으로 저장하고, 사용자가 확인하면 같은 meeting_id로
    다시 호출하며 finalize=true로 확정한다. 담당자나 기한이 빠진 Action Item, 검토되지 않은 Compliance 항목(compliance_flags의 open)이
    있거나 요청자에게 확정 권한(회의 의장 · 간사 또는 확정 권한 그룹)이 없으면 확정하지 않고 warnings로 알려 준다.
    확정된 회의록은 correction_reason과 함께 보낼 때만 정정되며, 이전 버전은 이력으로 남는다.
    확정 · 정정하면 참석자 회의록 메일과 담당자 업무 배정 메일을 Agent가 주소를 찾아 바로 보낸다 (mail_queued).
    결과의 meeting_id · Action Item id · compliance_flags를 사용자에게 알려 준다.

    Args:
        title: 회의명 (예: "AI 반도체 전략회의").
        date: 회의 날짜 YYYY-MM-DD.
        summary: 회의 요약 2~4문장.
        decisions: 결정 · 의결 · 기준 · 주요 발언 · 미결 쟁점 목록. transcript에 실제로 있는 것만.
        action_items: 후속 조치 목록. 담당 · 기한은 회의에서 정해진 경우만 채운다.
        attendees: 참석자 · 불참자 · 배석자 (모르면 비워 둔다).
        topics: 검색용 주제 키워드 (예: ["NeuroEdge", "M&A"]).
        body: 안건별 논의 경과 · 발언 요지 (서술, 선택).
        classification: 1 공개 · 2 사내 · 3 대외비 · 4 극비. 사용자가 정하지 않으면 2.
        location: 장소 (선택).
        meeting_id: 이미 저장된 초안 · 예약된 회의에 쓸 때 그 id. 새 회의면 비워 둔다.
        finalize: 사용자가 확정을 요청했을 때만 true.
        correction_reason: 확정된 회의록을 정정할 때의 사유.
        transcript_id: 녹음 자동 변환본 첫 줄의 transcript id (예: "TR-20261028-1A2B3C"). 회의에 발언 원문을 연결한다.
        flag_reviews: Compliance 검토 결과 — 사용자(간사)가 각 항목에 대해 판단을 말했을 때만.
    """
        viewer = current_viewer()
        return store_call(lambda: save_minutes(
            store=store, viewer=viewer, title=title, date=date, summary=summary,
            decisions=plain(decisions or []), action_items=plain(action_items or []), attendees=plain(attendees or []),
            topics=topics, body=body, classification=classification, location=location, meeting_id=meeting_id,
            finalize=finalize, correction_reason=correction_reason, transcript_id=transcript_id,
            flag_reviews=plain(flag_reviews or []),
        ))

    return save_meeting_minutes
