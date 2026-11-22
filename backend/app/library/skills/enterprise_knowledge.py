"""enterprise_knowledge — how the Advisory Agent uses the Enterprise KG (meetings ↔ news) in its advice."""

from __future__ import annotations

from ...runtime.capabilities import Skill
from ..tools.query_enterprise_kg import TOOL_NAME
from ..tools.run_mna_review import TOOL_NAME as REVIEW

ENTERPRISE_KNOWLEDGE = Skill(
    name="enterprise_knowledge",
    description="회의 기록과 뉴스를 잇는 기업 지식 그래프를 조회해, 과거 결정 · 기준과 외부 사건의 관계를 경영자문에 반영하는 방법.",
    instructions=f"""\
- 특정 기업의 인수 · 투자 검토, 또는 최근 뉴스와 과거 회의 결정 · 계획의 관계를 묻는 업무에서 축적된 지식이 필요하면 {TOOL_NAME}로 대상 기업을 조회한다
  (entities에 기업명, 판단을 좌우하는 상대 기업이 있으면 함께). 그래프는 위임 문장에 없는 연결(바뀐 결정, 관계의 종료 시점, 판정된 영향, 열린 후속 업무)과
  출처를 준다. 이번 실행에서 이미 같은 기업을 조회했거나 받은 정보로 충분하면 다시 조회하지 않는다. 일반 개념 질문에는 쓰지 않는다.
- 그래프는 저장된 기록을 주기적으로 연결한 것이다(as_of가 마지막 갱신). 최신 소식이 필요하거나, 판단을 좌우하는 관계의 상태가 불확실하면
  (종료 검토 · 보도 엇갈림 · 확인 필요) 뉴스 · 미팅 에이전트에게 원 출처를 확인한다.
- 결과는 근거별로 구분해 읽고 응답에서도 구분한다:
  decisions · follow_ups는 회의에서 정한 것(결정 ref와 회의 id로 인용), reported_facts는 기사가 전한 관계(state가 현재 상태다: status ended만 실제로 끝난 관계이고 valid_to가 그 시점이다.
  ends_on이 있으면 그 날짜까지 유효한 관계(만료 예정)이고, end_review_since가 있으면 종료가 검토 · 예고되었을 뿐 확정되지 않은 관계다.
  만료 예정이나 종료 검토를 '종료되었다'고 쓰지 않는다), assessed_impacts는 판정된 영향(effect broken이면 전제가 깨짐, at_risk면 흔들림, judged가 keywords면 낱말만 맞은 것이라 확인 필요),
  associations는 같은 기업을 다룰 뿐 영향이 판정되지 않은 연결이다. associations를 "영향을 주었다"고 단정하지 말고 "검토가 필요한 연관"으로 쓴다.
- 인수 · 투자 검토에서는 그래프에서 확인한 회의 기준(가격 상한 · 최소 Time-to-Market 등)을 {REVIEW}의 criteria로, 보도된 관계의 변화
  (예: 최대 고객과의 공급 관계 종료)를 scenarios의 재무 동인으로 옮겨 계산하고, 판단을 기록할 때 결정 ref · 기사 id를 근거로 인용한다.
- 그래프에 없는 사실을 만들지 않는다. found가 false거나 비어 있으면 그 사실을 밝히고 다른 Tool 결과로 답한다.
- 바뀐 결정(status superseded)은 현재 기준이 아니다. 지금 유효한 결정을 기준으로 쓰고, 바뀐 이력이 판단에 중요하면 함께 밝힌다.""",
)
