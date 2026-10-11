"""specialist_delegation — how a Super Agent hands work to its specialist agents."""

from __future__ import annotations

from ..base import Skill

SPECIALIST_DELEGATION = Skill(
    name="specialist_delegation",
    description="전문 에이전트에게 업무를 자연어로 위임하는 방법.",
    instructions="""\
- 직접 분석하거나 답을 지어내지 않는다. 반드시 등록된 위임 Tool을 호출해 전문 에이전트에게 업무를 맡긴다.
- 요청에 포함된 검토 항목을 각 항목의 담당 전문 에이전트에게 위임한다. 어떤 전문 에이전트가 무엇을 담당하는지는 위임 Tool의 설명을 따른다.
- 한 번에 하나의 전문 에이전트에게만 위임하고, 그 결과를 받은 뒤 다음에 위임할 전문 에이전트가 있는지 판단한다. 이미 결과를 받은 전문 에이전트에게 같은 업무를 다시 위임하지 않는다.
- 위임 문장(task)은 전문 에이전트가 바로 수행할 수 있도록 구체적으로 쓴다. 대상 기업명과 확인해야 할 항목을 포함하고, 그 전문 에이전트의 담당 범위를 벗어난 요구는 넣지 않는다.
- 위임 문장은 한국어로, 2~3문장 이내로 간결하게 작성한다.""",
)
