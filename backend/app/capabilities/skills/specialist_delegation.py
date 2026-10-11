"""specialist_delegation — how a Super Agent hands work to its specialist agents."""

from __future__ import annotations

from ..base import Skill

SPECIALIST_DELEGATION = Skill(
    name="specialist_delegation",
    description="전문 에이전트에게 업무를 자연어로 위임하는 방법.",
    instructions="""\
- 직접 분석하거나 답을 지어내지 않는다. 반드시 등록된 위임 Tool을 호출해 전문 에이전트에게 업무를 맡긴다.
- 위임 문장(task)은 전문 에이전트가 바로 수행할 수 있도록 구체적으로 쓴다. 대상 기업명, 찾아야 할 것(결정된 기준, 미완료 Action Item 등)을 포함한다.
- 위임 문장은 한국어로, 2~3문장 이내로 간결하게 작성한다. 전문 에이전트의 담당 범위를 벗어난 요구(외부 뉴스, 재무 분석 등)는 넣지 않는다.""",
)
