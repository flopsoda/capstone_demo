"""meeting_context_analysis — how the Meeting Agent finds and reports past meeting context."""

from __future__ import annotations

from ..base import Skill
from ..tools.run_meeting_context_retrieval import run_meeting_context_retrieval

MEETING_CONTEXT_ANALYSIS = Skill(
    name="meeting_context_analysis",
    description="현재 의사결정과 관련된 과거 회의의 결정사항과 미완료 Action Item을 찾아 정리하는 방법.",
    instructions=f"""\
- 회의 기록은 반드시 등록된 Workflow Tool({run_meeting_context_retrieval.name})로 조회한다. 기억이나 추측으로 회의 내용을 만들어내지 않는다.
- 현재 의사결정과 관련된 과거 회의를 찾고, 그 회의의 결정사항과 미완료 Action Item을 우선 확인한다. 관련 없는 회의 내용은 제외한다.
- Tool 결과에 없는 내용은 답하지 않는다. 관련 회의가 없으면 그렇게 말한다. 기록에 없는 항목(기한, 참석자 등)은 언급하지 않는다.
- 최종 응답 형식 (순서 고정):
  1) 회의 식별 (날짜 · 회의명)
  2) 결정된 기준 — 발언자별로 한 줄씩
  3) 미완료 Action Item — 목록
  불필요한 서론과 결론은 생략하고 간결하게 작성한다.""",
)
