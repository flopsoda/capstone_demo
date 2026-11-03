"""decision_review — how the Meeting Agent keeps decisions in force after the meeting (premises, new plans, re-review)."""

from __future__ import annotations

from ...runtime.capabilities import Skill
from ..tools.run_decision_review import TOOL_NAME as REVIEW
from ..tools.run_meeting_schedule import TOOL_NAME as SCHEDULE

DECISION_REVIEW = Skill(
    name="decision_review",
    description="회의가 끝난 뒤의 결정 관리: 결정을 지금도 따라도 되는지, 전제가 흔들렸는지, 새 계획 · 제안이 기존 결정과 맞는지 확인하고 재검토 회의를 제안하는 방법.",
    instructions=f"""\
- 결정을 지금도 따라도 되는지("계속 가도 돼?", "그 결정 아직 유효해?"), 전제가 흔들린 결정, 새 계획 · 제안 · 투자안 · 금액이 기존 결정과
  맞는지를 물으면 {REVIEW}로 확인한다. 새 계획이면 받은 내용(금액 포함)을 query에 그대로 넣는다. 기억이나 추측으로 답하지 않는다.
- 응답 순서 (Tool 결과에서만 가져온다):
  1) 관련 결정 — ref · 회의명 · 날짜와 근거 발언(source, 예: "[00:41] 김민수 (CEO)")
  2) 전제 · 조건과 지금 상태 — 유지 · 흔들림 · 깨짐 · 달성. 흔들렸으면 그 신호(뉴스 · 보고의 제목 · 날짜)와 판단 이유, 전제를 말한 사람과 발언 시각
  3) 새 계획과 결정의 차이 — conflicts의 결정 금액 · 계획 금액 · 초과액 (계획이 있을 때)
  4) 판단 — 결정을 그대로 따를 수 있는지, 재검토가 필요한지. 근거는 1~3만 쓴다
- judged가 keywords인 신호는 Agent가 판단하지 못하고 낱말만 맞춘 것이라 "확인 필요"로 밝힌다. 결정이 바뀌었으면(superseded_by) 바뀐 결정을 기준으로 쓴다.
- re_review가 있으면 재검토 회의를 제안한다: 회의명, 안건(목록), 참석 대상을 보여 주고 날짜 · 시각을 묻는다.
  사용자가 시각을 정하면 {SCHEDULE} action=create에 title · starts_at · attendees · agenda와 carry_over_from(re_review.carry_over_from)을
  넣어 예약한다. 사용자가 시각을 정하기 전에는 예약하지 않는다.
- scheduled_review가 있으면 재검토 회의가 이미 잡혀 있는 것이다. 새로 제안하지 않고 그 회의(id · 일시 · 장소 · 안건)를 알린다.
  새로 생긴 문제(conflicts 등)가 그 회의 안건에 없으면 안건에 더할지 묻는다 ({SCHEDULE} action=update의 agenda).""",
)
