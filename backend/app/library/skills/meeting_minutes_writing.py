"""meeting_minutes_writing — how the Meeting Agent turns a transcript into saved minutes."""

from __future__ import annotations

from ...runtime.capabilities import Skill
from ..tools.manage_mail_outbox import TOOL_NAME as OUTBOX
from ..tools.run_meeting_schedule import TOOL_NAME as SCHEDULE
from ..tools.save_meeting_minutes import TOOL_NAME as SAVE
from ..tools.update_action_item import TOOL_NAME as UPDATE

MEETING_MINUTES_WRITING = Skill(
    name="meeting_minutes_writing",
    description="회의 transcript(녹음 자동 변환본 포함)나 메모로 회의록을 작성해 초안 저장 → Compliance 검토 · 확인 → 확정하는 방법.",
    instructions=f"""\
- 회의록은 {SAVE}로만 저장한다. 저장하지 않은 회의록을 저장됐다고 말하지 않는다.
- transcript에 실제로 있는 내용만 쓴다. 실제로 합의된 것은 decision, 표결로 정한 것은 resolution(결과 · 찬반 수 · 반대 이유),
  이후 판단 기준은 criterion, 주요 발언은 opinion, 결론 없이 끝난 논의나 남은 질문은 issue로 나눈다.
- 발언자는 transcript 라벨(예: "박지훈 (CFO)")을 쓴다. "화자 A" · "화자 B" …(예전 변환본은 "미확인 화자 N")는 목소리가 등록되지 않은 사람이고
  같은 글자는 같은 목소리다. 이름을 지어내지 말고 참석자 · 발언자 · 담당자에 라벨 그대로 쓴다. 발언 속 호칭이나 자기소개로 누구인지 짐작되어도
  라벨은 바꾸지 않고 "확인 필요"에 "화자 B = 박 부장으로 보임"처럼 적는다. 화자 라벨뿐이어도 이름을 먼저 묻지 않고 초안을 저장한다.
  담당이 화자 라벨인 할 일은 확정 전에 실제 담당자를 정해야 한다 (회의 카드의 '참석자 확인'에서 목소리의 이름을 정하거나 '담당·기한'으로).
- 담당자 · 기한은 회의에서 정해진 경우만 채운다. 추측해서 채우지 않는다. 모두가 각자 하는 일("각자 ~ 준비")은 담당을 비워 두고
  회의 카드에서 '참석자마다 나누기'로 나눌 수 있다고 알린다 (담당에 "각자" · "전원"을 적지 않는다).
- 녹음 요청에 "출석 확인: CHK-…"가 있으면 그 코드를 checkin_code로 보낸다. 그 QR로 출석한 사람은 말하지 않았어도 참석자로 들어간다.
  저장 결과의 attendance로 참석자를 확인한다: people(출석 checked_in · 발언 spoke), unknown_speakers(이름을 정해야 할 목소리), silent(말하지 않은
  참석자), suggestion(목소리 하나 · 출석했지만 말하지 않은 사람 하나라 그 사람으로 보이는 경우). 응답의 "참석자 확인"에 출석 인원과 말하지 않은
  참석자를 적고, 이름을 정해야 할 목소리와 추정이 있으면 "화자 C = 이서연이 맞나요?"처럼 묻는다. transcript에서 이름이 불렸지만 출석도 발언도
  확인되지 않은 사람이 있으면 "말하지 않은 참석자가 더 있었나요? (예: 발언에서 언급된 최유진)"처럼 묻는다. 참석자는 회의 카드에서 고칠 수 있다.
- 음성 자동 변환본은 이름과 숫자(금액, 날짜)를 잘못 들을 수 있다. 의심되는 부분은 확인 필요 사항으로 표시한다.
- 녹음 변환본은 요청이 오기 전에 잘못 들린 용어 · 이름과 띄어쓰기가 자동으로 교정된다. 변환본 뒤에 "[자동 교정] …"이 있으면 그 변환본으로 쓰고,
  "[자동 교정 실패] …"가 있으면 그 지시대로 회의록을 쓰기 전에 correct_transcript를 한 번 불러 돌려받은 lines로 쓴다. 그것도 실패하면 받은
  변환본으로 쓰고, 응답에 변환본 교정이 끝나지 않았다고 적는다. correct_transcript는 이 경우가 아니면 부르지 않는다.
- 자동 변환본 첫 줄에 "[transcript_id: TR-…]"가 있으면 그 id를 transcript_id로 보내 발언 원문을 회의에 연결한다. 연결된 transcript가 있으면
  결정 · Action Item마다 근거 발언 줄의 시각(예: "03:15")을 source_time에 적는다. id를 지어내지 않는다.
- 저장 결과의 compliance_flags 중 status가 open인 항목은 법적 검토가 필요할 수 있는 발언이다. 항목마다 id · 위치 · 규칙 · 발췌를 그대로 보여 주고
  간사의 판단(문제없음 → cleared, 조치함 → acknowledged와 조치 내용)을 받는다. 판단을 대신 내리지 않으며, 사용자가 말한 판단만 flag_reviews로 보낸다.
  문제가 된 문장을 회의록에서 고치거나 빼라고 하면 summary · body · decisions를 고쳐 다시 저장한다 (그 항목은 다시 검사된다).
- 처음에는 항상 초안으로 저장한다(finalize=false). 사용자가 확정을 명시적으로 요청할 때만 같은 meeting_id로 finalize=true를 보내고,
  그 사이 사용자가 알려 준 담당 · 기한 · 정정 · Compliance 판단은 그 호출에 반영한다. 확정되지 않았으면 warnings에 적힌 이유(담당 · 기한 누락,
  검토 안 된 Compliance 항목, 확정 권한 없음)를 그대로 알린다. 확정 권한이 없는 사용자에게는 간사나 의장에게 확정을 요청하라고 안내한다.
- 확정 · 정정하면 참석자 회의록 메일과 담당자 업무 배정 메일을 Agent가 바로 보낸다(mail_queued). 회의록 메일에는 회의록 PDF가,
  의장 · 간사에게는 회의 결과 보고서 PDF도 첨부되며, 두 문서는 회의 업무 화면에서도 내려받을 수 있다. 받는 사람과 상태를 알리고,
  주소를 찾지 못한 사람(needs_address)이 있으면 매일 아침 다시 찾는다고 알린다. 승인을 요청하지 않는다 (메일 조회 · 정정은 {OUTBOX}).
- 확정된 회의록을 고쳐 달라고 하면 정정 사유를 확인한 뒤 correction_reason과 함께 저장한다. Action Item의 진행 상태 변경은 회의록 정정이 아니라 {UPDATE}로 한다.
- 예약된 회의({SCHEDULE})의 회의록이면 그 meeting_id에 저장한다.
- 회의 녹음 요청에 "회의: <meeting_id>"가 있으면(회의 카드에서 녹음을 시작한 경우) 그 meeting_id에 초안을 저장한다.
  예약된 회의에 저장할 때는 start_time · end_time을 보내지 않아 예약 시각을 그대로 둔다 (사용자가 실제 시각을 말한 경우만 보낸다).
- 회의 녹음이 끝나 자동으로 전달된 요청("녹음: 날짜 시작~끝")에 회의 id가 없으면 먼저 {SCHEDULE} action=list(date_from=그 날짜, date_to=그 날짜)로
  녹음 시각과 겹치거나 가장 가까운(앞뒤 1시간 안) 예약된 회의를 찾는다. 하나면 그 meeting_id와 회의명으로(시각은 예약 그대로) 초안을 저장하고 응답에
  어느 회의에 저장했는지 밝힌다. 없거나 여럿이면 새 회의로 저장하고(회의명은 논의 내용으로 짧게, start_time · end_time은 녹음 시각)
  "확인 필요"에 그 사실을 적는다.
  사용자에게 먼저 묻지 않고 초안까지 저장한다 (확정은 여전히 사용자가 요청할 때만).
- 녹음 파일을 올린 요청("녹음 파일: 이름", transcript는 [첨부 파일 내용])은 녹음 시각을 모른다. 파일 이름이나 transcript에 날짜가
  있으면(예: "캡스톤_1030" → 10월 30일) 그 날짜의 예약된 회의를 위와 같이 찾고, 없으면 그 날짜로 새 회의를 저장한다. 날짜를 알 수
  없으면 오늘 날짜로 저장한다. start_time · end_time은 보내지 않고, "확인 필요"에 회의 날짜 · 시각을 확인해 달라고 적는다.
  이 경우에도 사용자에게 먼저 묻지 않고 초안까지 저장한다.
- 이전 회의의 결정을 바꾼 결정은 supersedes에, 이전 쟁점을 해결한 결정은 resolves에 그 ref(조회 결과의 ref)를 적는다.
- 저장 결과의 recall은 이번 참석자들이 이전 회의에서 맡아 아직 열린 일(open_items, 기한 지난 것 먼저)과 같은 사람들이 있던 회의의
  미결 쟁점(open_issues)이다. 이번 회의에서 해결되거나 바뀐 것이 있으면 resolves · supersedes를 넣어 다시 저장하고, 나머지는 응답의
  "이전 회의에서 이어지는 일"에 id · 담당 · 기한(지난 일수)과 함께 짧게 적는다. 없으면 그 부분을 쓰지 않는다.
- 응답 형식: 첫 줄에 "회의록 초안 저장: <meeting_id>" 또는 "회의록 확정: <meeting_id>", 둘째 줄에 회의명과 일시(날짜 · 시작~종료 시각,
  저장 결과의 date · time). 이어서 요약, 결정사항(발언자),
  Action Item(id · 담당 · 기한), 참석자 확인(출석 인원 · 말하지 않은 참석자 · 이름을 정해야 할 목소리), 이전 회의에서 이어지는 일(recall이 있을 때),
  Compliance 검토 항목(open이 있을 때), 보낸 메일(확정 · 정정했을 때),
  마지막에 "확인 필요" 목록(Tool 결과의 warnings 포함). 초안이면 참석자 질문과 확정 여부를 묻는 문장으로 끝낸다.""",
)
