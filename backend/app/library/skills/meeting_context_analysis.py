"""meeting_context_analysis — how the Meeting Agent finds and reports past meeting context."""

from __future__ import annotations

from ...runtime.capabilities import Skill
from ..tools.run_meeting_context_retrieval import TOOL_NAME

MEETING_CONTEXT_ANALYSIS = Skill(
    name="meeting_context_analysis",
    description="현재 의사결정과 관련된 과거 회의의 결정사항과 미완료 Action Item을 찾아 정리하는 방법.",
    instructions=f"""\
- 회의 기록은 반드시 등록된 Workflow Tool({TOOL_NAME})로 조회한다. 기억이나 추측으로 회의 내용을 만들어내지 않는다.
- 현재 의사결정과 관련된 과거 회의를 찾고, 그 회의의 결정사항과 미완료 Action Item을 우선 확인한다. 관련 없는 회의 내용은 제외한다.
- Tool 결과에 없는 내용은 답하지 않는다. 관련 회의가 없으면 그렇게 말한다. 기록에 없는 항목(기록되지 않은 기한, 참석자 등)은 언급하지 않는다.
- 조회 결과에는 요청자가 열람할 수 있는 등급의 회의만 들어 있다. 찾지 못했으면 "열람 가능한 회의 기록에서 찾지 못했다"고만 말하고, 다른 기록이 있을 것이라고 추측하지 않는다.
- 최종 응답 형식 (순서 고정):
  1) 회의 식별 (날짜 · 회의명)
  2) 결정된 기준 — 발언자별로 한 줄씩
  3) 미완료 Action Item — 목록 (기한이 지났으면 지연 일수와 함께)
  불필요한 서론과 결론은 생략하고 간결하게 작성한다.
- 다가오는 회의에 앞서 정리(브리핑)를 요청받으면 위 형식 대신 다음 순서로 쓴다. 모든 항목은 Tool 결과에서만 가져온다.
  1) 지난 결정 · 기준 (날짜 · 발언자) — 바뀐 결정이 있으면 최신 것을 쓰고 바뀌었다고 밝힌다
  2) 후속 조치 진행 — 지연 · 진행 중 · 완료
  3) 해결되지 않은 쟁점
  4) 이번 회의에서 확인할 질문 — 위 1~3에서 도출""",
)
