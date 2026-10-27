"""meeting_minutes_writing — how the Meeting Agent turns a transcript into saved minutes."""

from __future__ import annotations

from ...runtime.capabilities import Skill
from ..tools.run_meeting_schedule import TOOL_NAME as SCHEDULE
from ..tools.save_meeting_minutes import TOOL_NAME as SAVE
from ..tools.update_action_item import TOOL_NAME as UPDATE

MEETING_MINUTES_WRITING = Skill(
    name="meeting_minutes_writing",
    description="회의 transcript(녹음 자동 변환본 포함)나 메모로 회의록을 작성해 초안 저장 → 확인 → 확정하는 방법.",
    instructions=f"""\
- 회의록은 {SAVE}로만 저장한다. 저장하지 않은 회의록을 저장됐다고 말하지 않는다.
- transcript에 실제로 있는 내용만 쓴다. 실제로 합의된 것은 decision, 표결로 정한 것은 resolution(결과 · 찬반 수 · 반대 이유),
  이후 판단 기준은 criterion, 주요 발언은 opinion, 결론 없이 끝난 논의나 남은 질문은 issue로 나눈다.
- 발언자는 transcript 라벨(예: "박지훈 (CFO)")이나 발언 속 호칭으로 확인될 때만 쓴다. "미확인 화자 N"은 이름을 지어내지 말고 그대로 두며 확인 필요 사항으로 알린다.
- 담당자 · 기한은 회의에서 정해진 경우만 채운다. 추측해서 채우지 않는다.
- 음성 자동 변환본은 이름과 숫자(금액, 날짜)를 잘못 들을 수 있다. 의심되는 부분은 확인 필요 사항으로 표시한다.
- 처음에는 항상 초안으로 저장한다(finalize=false). 사용자가 확정을 명시적으로 요청할 때만 같은 meeting_id로 finalize=true를 보내고,
  그 사이 사용자가 알려 준 담당 · 기한 · 정정은 그 호출에 반영한다. 담당 · 기한이 빠져 확정되지 않았으면 무엇이 빠졌는지 알린다.
- 확정된 회의록을 고쳐 달라고 하면 정정 사유를 확인한 뒤 correction_reason과 함께 저장한다. Action Item의 진행 상태 변경은 회의록 정정이 아니라 {UPDATE}로 한다.
- 예약된 회의({SCHEDULE})의 회의록이면 그 meeting_id에 저장한다.
- 이전 회의의 결정을 바꾼 결정은 supersedes에, 이전 쟁점을 해결한 결정은 resolves에 그 ref(조회 결과의 ref)를 적는다.
- 응답 형식: 첫 줄에 "회의록 초안 저장: <meeting_id>" 또는 "회의록 확정: <meeting_id>". 이어서 요약, 결정사항(발언자),
  Action Item(id · 담당 · 기한), 마지막에 "확인 필요" 목록(Tool 결과의 warnings 포함). 초안이면 확정 여부를 묻는 문장으로 끝낸다.""",
)
