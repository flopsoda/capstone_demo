"""save_meeting_minutes — agent-visible Tool surface over the meeting_minutes workflow.

Shared library: a system binds its meeting store with make_save_meeting_minutes(store).
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import BaseTool, tool
from pydantic import AliasChoices, BaseModel, Field

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


class Premise(BaseModel):
    kind: Literal["premise", "condition"] = Field(
        default="premise",
        description="premise 전제: 결정이 맞으려면 계속 사실이어야 하는 바깥 상황 (시장 가격 · 수요 · 금리 · 규제 등) · "
                    "condition 조건: 회사가 기한까지 해내야 하는 목표 (수율 · 일정 · 인력 등)",
    )
    statement: str = Field(description="전제 · 조건의 내용 (예: 'HBM 단가가 내년 말까지 현재 수준 유지', '내년 3월까지 수율 85% 달성')")
    review_when: str | None = Field(default=None, description="회의에서 밝힌 재검토 기준 (예: '단가가 15% 넘게 떨어지면 투자 규모 재검토'). "
                                                              "재검토 기준은 따로 전제 · 조건으로 만들지 않고 그 전제의 이 칸에 적는다")
    watch: list[str] = Field(default_factory=list,
                             description="뉴스 · 보고에서 이 전제를 찾을 낱말 2~4개, 한 낱말씩 (예: ['HBM', '고대역폭 메모리', '단가'])")
    due: str | None = Field(default=None, description="condition만: 달성 기한 YYYY-MM-DD. 회의에서 정해진 경우만 ('내년'은 회의 날짜의 다음 해)")
    speaker: str | None = Field(default=None, description="그 전제 · 조건을 말한 사람 (transcript 라벨)")
    source_time: str | None = Field(default=None, description="그 전제 · 조건을 말한 발언의 transcript 시각 (예: '00:35'). transcript가 있으면 적는다")


class Decision(BaseModel):
    kind: Literal["decision", "resolution", "criterion", "opinion", "issue"] = Field(
        description="decision 회의가 정한 것(\"~로 결정하겠습니다\", \"~를 승인하겠습니다\") · resolution 표결로 의결 · criterion 이후 판단 기준 · "
                    "opinion 주요 발언(보고 · 비교 결과 · 근거 · 의견) · issue 결론 내지 못한 쟁점/질문 (\"아직 모릅니다\", \"다시 보겠습니다\")",
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
    premises: list[Premise] = Field(
        default_factory=list,
        description="decision · resolution만: 회의에서 밝힌 이 결정의 전제와 조건 (다른 사람이 말한 것도 이 결정에 대한 것이면 여기에). "
                    "전제 · 조건을 따로 결정으로 만들지 않는다. 회의가 끝난 뒤 뉴스 · 보고로 전제가 흔들리는지 Agent가 지켜본다",
    )


class ActionItem(BaseModel):
    # A decision's field is `statement`; models often reuse that name for an item's text (then fail the call
    # again and again until the run's step limit). Both names are read; the schema shows `text`.
    text: str = Field(validation_alias=AliasChoices("text", "statement"), description="해야 할 일")
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
        classification: int | None = None,
        location: str | None = None,
        meeting_id: str | None = None,
        finalize: bool = False,
        correction_reason: str | None = None,
        transcript_id: str | None = None,
        flag_reviews: list[FlagReview] | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        checkin_code: str | None = None,
        subject: str | None = None,
    ) -> dict[str, Any]:
        """회의록을 회의 기록 저장소에 저장한다. 처음에는 초안(draft)으로 저장하고, 사용자가 확인하면 같은 meeting_id로
    다시 호출하며 finalize=true로 확정한다. 담당자나 기한이 빠진 Action Item, 검토되지 않은 Compliance 항목(compliance_flags의 open)이
    있거나 요청자에게 확정 권한(회의 의장 · 간사 또는 확정 권한 그룹)이 없으면 확정하지 않고 warnings로 알려 준다.
    확정된 회의록은 correction_reason과 함께 보낼 때만 정정되며, 이전 버전은 이력으로 남는다.
    확정 · 정정하면 참석자 회의록 메일과 담당자 업무 배정 메일을 Agent가 주소를 찾아 바로 보낸다 (mail_queued).
    결과의 meeting_id · Action Item id · compliance_flags를 사용자에게 알려 준다.
    녹음을 올릴 때 QR로 출석한 사람은 말하지 않았어도 참석자로 들어간다. 결과의 attendance(출석 · 발언 여부, 이름을 정해야 할
    목소리 unknown_speakers, 말하지 않은 참석자 silent, 추정 suggestion)와 recall(이번 참석자들이 이전 회의에서 맡아 아직 열린 일,
    미결 쟁점)을 보고 사용자에게 확인할 것을 묻는다.

    Args:
        title: 회의명 (예: "AI 반도체 전략회의").
        date: 회의 날짜 YYYY-MM-DD.
        summary: 회의 요약 2~4문장.
        decisions: 결정 · 의결 · 기준 · 주요 발언 · 미결 쟁점 목록. transcript에 실제로 있는 것만. 결정에 전제 · 조건이 말해졌으면 premises에.
            저장된 회의(meeting_id)를 다시 저장할 때 빼면 저장된 목록을 그대로 둔다. 보내면 보낸 목록으로 바뀐다.
        action_items: 후속 조치 목록. 담당 · 기한은 회의에서 정해진 경우만 채운다. 다시 저장할 때 빼면 그대로 둔다.
        attendees: 참석자 · 불참자 · 배석자 (모르면 비워 둔다).
        topics: 검색용 주제 키워드 (예: ["NeuroEdge", "M&A"]). 저장된 회의를 다시 저장할 때 빼면 그대로 둔다.
        body: 안건별 논의 경과 · 발언 요지 (서술, 선택).
        classification: 1 공개 · 2 사내 · 3 대외비 · 4 극비. 사용자가 등급을 말했을 때만 보낸다.
            새 회의에서 빼면 2, 저장된 회의를 다시 저장할 때 빼면 저장된 등급을 그대로 둔다.
        location: 장소 (선택).
        meeting_id: 이미 저장된 초안 · 예약된 회의에 쓸 때 그 id. 새 회의면 비워 둔다.
        finalize: 사용자가 확정을 요청했을 때만 true.
        correction_reason: 확정된 회의록을 정정할 때의 사유.
        transcript_id: 녹음 자동 변환본 첫 줄의 transcript id (예: "TR-20261028-1A2B3C"). 회의에 발언 원문을 연결한다.
        flag_reviews: Compliance 검토 결과 — 사용자(간사)가 각 항목에 대해 판단을 말했을 때만.
        start_time: 회의 시작 시각 HH:MM (한국 시간). 녹음 시각이나 회의에서 밝힌 시각만. 예약된 회의는 비워 두면 예약 시각을 쓴다.
        end_time: 회의 종료 시각 HH:MM (한국 시간). start_time과 같은 기준.
        checkin_code: 요청에 적힌 출석 확인 코드 (예: "CHK-7F3K2Q"). 그 QR로 출석한 사람을 참석자로 넣는다.
        subject: 이 회의가 이어 가는 사업 · 안건의 짧은 이름 (예: "천안 신공장", "뉴로엣지 인수"). 모르면 비워 둔다 —
            회의명으로 진행 중인 주제를 찾고, 없으면 새 주제를 만든다.
    """
        viewer = current_viewer()
        return store_call(lambda: save_minutes(
            store=store, viewer=viewer, title=title, date=date, summary=summary,
            decisions=None if decisions is None else plain(decisions),
            action_items=None if action_items is None else plain(action_items), attendees=plain(attendees or []),
            topics=topics, body=body, classification=classification, location=location, meeting_id=meeting_id,
            finalize=finalize, correction_reason=correction_reason, transcript_id=transcript_id,
            flag_reviews=plain(flag_reviews or []), start_time=start_time, end_time=end_time, checkin_code=checkin_code, subject=subject,
        ))

    return save_meeting_minutes
