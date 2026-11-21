"""enterprise_knowledge — how the Advisory Agent uses the Enterprise KG (meetings ↔ news) in its advice."""

from __future__ import annotations

from ...runtime.capabilities import Skill
from ..tools.query_enterprise_kg import TOOL_NAME
from ..tools.run_mna_review import TOOL_NAME as REVIEW

ENTERPRISE_KNOWLEDGE = Skill(
    name="enterprise_knowledge",
    description="회의 기록과 뉴스를 잇는 기업 지식 그래프를 조회해, 과거 결정 · 기준과 외부 사건의 관계를 경영자문에 반영하는 방법.",
    instructions=f"""\
- 특정 기업의 인수 · 투자 검토, 또는 최근 뉴스와 과거 회의 결정 · 계획의 관계를 묻는 업무면 먼저 {TOOL_NAME}로 대상 기업을 조회한다
  (entities에 기업명). 일반 개념 질문에는 쓰지 않는다.
- 결과는 근거별로 구분해 읽고 응답에서도 구분한다:
  decisions · follow_ups는 회의에서 정한 것(결정 ref와 회의 id로 인용), reported_facts는 기사가 전한 관계(status가 ended면 끝난 관계이고
  valid_to가 그 시점이다), assessed_impacts는 판정된 영향(effect broken이면 전제가 깨짐, at_risk면 흔들림, judged가 keywords면 낱말만 맞은 것이라 확인 필요),
  associations는 같은 기업을 다룰 뿐 영향이 판정되지 않은 연결이다. associations를 "영향을 주었다"고 단정하지 말고 "검토가 필요한 연관"으로 쓴다.
- 인수 · 투자 검토에서 {REVIEW}를 부를 때는 그래프에서 확인한 회의 기준(가격 상한 · 조건 등), 보도된 관계의 변화(예: 최대 고객과의 공급 관계 종료),
  판정된 영향을 출처(결정 ref · 회의 id · 기사 날짜와 매체)와 함께 context에 넣는다. 위임받은 업무 문장에 같은 내용이 있으면 중복 없이 합친다.
- 그래프에 없는 사실을 만들지 않는다. found가 false거나 비어 있으면 그 사실을 밝히고 다른 Tool 결과로 답한다.
- 바뀐 결정(status superseded)은 현재 기준이 아니다. 지금 유효한 결정을 기준으로 쓰고, 바뀐 이력이 판단에 중요하면 함께 밝힌다.""",
)
