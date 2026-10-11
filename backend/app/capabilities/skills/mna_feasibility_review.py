"""mna_feasibility_review — how the Advisory Agent reviews an acquisition proposal."""

from __future__ import annotations

from ..base import Skill
from ..tools.run_mna_review import run_mna_review

MNA_FEASIBILITY_REVIEW = Skill(
    name="mna_feasibility_review",
    description="제공된 기업 정보와 대화 맥락을 바탕으로 인수안의 재무·전략·기술 타당성과 리스크를 경영자 관점에서 검토하는 방법.",
    instructions=f"""\
- 실제 기업에 대한 인수 검토(재무 타당성, 전략적 가치, 기술적 의미, 리스크, 권고)는 반드시 등록된 Workflow Tool({run_mna_review.name})로 수행한다.
  데이터에 없는 기업 사실(매출, 가격, 고객, 소송 등)을 기억이나 추측으로 만들어내지 않는다.
  일반적인 M&A 개념, 평가 방법론, 경영 전략에 대한 질문은 Tool 없이 답해도 된다.
- 요청과 대화 맥락에서 대상 기업, 검토 목적, 평가 관점(persona: CEO / CFO / CTO)을 파악해 Tool 인자로 전달한다.
  한 번의 검토는 하나의 관점으로 Tool을 한 번만 호출해 수행한다. 관점이 명시되지 않았거나 여러 관점이 언급되면
  의사결정 주체에 가장 가까운 관점 하나를 고른다 (계열사 차원의 인수 추진 여부 판단이면 CEO).
- 요청에 함께 전달된 맥락(과거 회의에서 정한 기준, 외부에서 확인된 리스크, 조건 등)은 그대로 context 인자에 넣는다. 요약하거나 빼지 않는다.
- 선택된 persona의 관점을 우선 반영한다. Tool 결과의 평가 항목 순서가 그 관점의 우선순위이며, 응답도 그 순서를 따른다.
- Tool 결과에 없는 내용은 답하지 않는다. 권고와 조건은 Tool이 반환한 값을 그대로 쓰고 임의로 바꾸지 않는다.
- 최종 응답 형식 (순서 고정):
  1) 권고 — 한 줄 (평가 관점 포함)
  2) 핵심 판단 근거 — 목록
  3) 주요 우려 — 목록 (수준 포함)
  4) 권고 조건 — 목록
  불필요한 서론과 결론은 생략한다.""",
)
