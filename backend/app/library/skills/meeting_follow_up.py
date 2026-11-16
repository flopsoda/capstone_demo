"""meeting_follow_up — how the Meeting Agent tracks Action Items and keeps the meeting calendar."""

from __future__ import annotations

from ...runtime.capabilities import Skill
from ..tools.manage_mail_outbox import TOOL_NAME as OUTBOX
from ..tools.run_action_item_tracking import TOOL_NAME as TRACK
from ..tools.run_meeting_schedule import TOOL_NAME as SCHEDULE
from ..tools.update_action_item import TOOL_NAME as UPDATE

MEETING_FOLLOW_UP = Skill(
    name="meeting_follow_up",
    description="회의 후속 관리: Action Item 진행 추적 · 갱신, 다음 회의 일정 관리, 회의 메일 자동 발송 확인.",
    instructions=f"""\
- 진행 상황 질문(지연된 일, 마감 임박, 담당별 현황, 내 할 일)은 {TRACK}으로 조회한다. 본인 담당을 물으면 mine=true,
  "이번 주 마감" · "내일까지"처럼 기간을 물으면 due_within_days를 쓴다. 날짜를 직접 계산하지 않는다.
- 진행 보고는 기준일(as_of)을 밝히고, 지연 항목(지연 일수 큰 순) → 마감 임박 → 그 외 순으로, 담당별로 묶어 쓴다.
  각 항목은 id · 내용 · 담당 · 기한 · 상태. 최근 진행 메모(last_note)가 있으면 덧붙인다.
- 상태 · 기한 · 담당 변경은 사용자가 명시적으로 말한 것만 {UPDATE}로 반영한다. 완료 여부를 추측하지 않는다.
  대상 id가 분명하지 않으면 먼저 조회하거나 결과의 candidates를 보여 주고 묻는다. 반영 후에는 바뀐 내용과 이력을 짧게 알린다.
  확정된 회의록의 할 일 담당 · 기한을 바꾸면 새 담당자에게 업무 배정 메일이 바로 나간다 (결과의 mail_queued를 알린다).
- "독촉해 줘" · "재촉 메일 보내 줘"처럼 담당자에게 지금 알림을 요청하면 할 일마다 {UPDATE}에 remind=true로 보낸다 (사람 이름만 말하면
  먼저 {TRACK}으로 그 사람의 열린 할 일을 찾는다). 받는 사람 · 제목을 알리고, remind_skipped가 있으면 그 이유를 그대로 전한다.
- 회의 일정 조회 · 예약 · 변경 · 취소는 {SCHEDULE}로 한다. "내일" · "다음 주 화요일" 같은 상대 날짜는 먼저 action=list를 호출해
  결과의 today를 보고 계산한다. 변경 · 취소 전에는 대상 회의와 바뀌는 내용을 사용자에게 확인받는다.
- 예약 요청은 먼저 action=draft로 점검한다 (저장 · 메일 없음). 사용자가 말한 것만 넣는다: 시각을 말하지 않았으면 starts_at에 날짜만,
  참석자를 말하지 않았으면 attendees를 비운다. 빈 값을 지어내지 않는다 — 기록으로 채울 수 있는 것은 draft가 채우고 assumed로 알린다.
  · draft의 complete가 true면 (회의명 · 날짜 · 시작 시각 · 참석자를 사용자가 분명히 말했고 겹치는 회의가 없음) draft의 값으로 바로 create한다.
    지난 회의에서 이어지는 안건이 있으면 carry_over_from에 draft의 carry_over_from을 쓴다.
  · complete가 false면 create하지 않는다. 화면에 예약 양식이 뜨므로 빠진 것(missing) · 애매한 이름(unclear, 후보를 보여 줌) ·
    겹치는 회의(conflicts) · 가정한 것(assumed)만 짧게 알리고 "양식에서 고치고 '예약하고 안내 메일 보내기'를 누르거나 답해 달라"고 한다.
    사용자가 답하면 그 값으로 다시 draft하고, complete가 되면 create한다.
- 이전 회의에서 보류된 안건이나 해결되지 않은 쟁점을 다음 회의로 넘길 때는 create에 carry_over_from(이전 회의 id)을 쓴다.
  "지난 회의"에 해당하는 회의가 여럿이면 날짜가 가장 최근인 회의를 고르고, 확인 단계에서 그 회의의 id와 날짜를 밝힌다.
- 회의 예약 · 변경 · 취소는 참석 대상에게 안내 메일을, 매일 아침 작업은 기한 임박 · 경과 Action Item의 담당자에게 알림 메일을 보낸다.
  메일은 Agent가 받는 사람의 주소를 스스로 찾아 승인 없이 바로 보낸다. 사용자에게 주소를 묻거나 승인을 요청하지 않는다.
- 메일 질문("메일 보냈어?", "알림 확인")은 {OUTBOX} action=list로 보여 준다: 메일 id · 종류 · 받는 사람 · 제목 · 상태(보냄 시각), 요청하면 본문.
  주소를 찾지 못한 메일(needs_address)은 매일 아침 다시 찾는다고 알린다. 사용자가 먼저 주소를 알려 주면 그 주소로 set_address한다. 주소를 추측하지 않는다.
  다시 보내기(resend) · 취소(reject)는 사용자가 대상(메일 id 또는 회의)을 분명히 말했을 때만 한다.
- smtp_configured가 false면 메일 서버가 설정될 때까지 발송 대기로 남는다고 알린다.""",
)
