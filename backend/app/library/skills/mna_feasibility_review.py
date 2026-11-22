"""mna_feasibility_review — how the Advisory Agent reviews an acquisition proposal
(single perspective, CEO·CFO·CTO summary, What-if scenarios)."""

from __future__ import annotations

from ...runtime.capabilities import Skill
from ..tools.query_enterprise_kg import TOOL_NAME as KG_TOOL
from ..tools.run_mna_review import TOOL_NAME

MNA_FEASIBILITY_REVIEW = Skill(
    name="mna_feasibility_review",
    description=(
        "제공된 기업 정보와 대화 맥락을 바탕으로 인수안의 재무·전략·기술 타당성과 리스크를 경영자 관점에서 검토하고, "
        "인수가격·수익성·고객 의존도 변경 시나리오(What-if)를 비교하는 방법."
    ),
    instructions=f"""\
Tool 사용:
- 실제 기업에 대한 인수 검토(재무 타당성, 전략적 가치, 기술적 의미, 리스크, 권고)와 What-if 분석은 반드시 등록된 Workflow Tool({TOOL_NAME})로 수행한다.
  그 전에 먼저 {KG_TOOL}로 대상 기업을 조회해(위임받은 문장에 맥락이 있어도) 확인한 회의 기준 · 관계 변화 · 판정된 영향을 출처와 함께 context에 넣는다 (enterprise_knowledge 지침).
  기업 데이터가 없을 것 같아도 추측하지 말고 Tool로 확인한다. 일반적인 M&A 개념, 평가 방법론, 경영 전략에 대한 질문은 Tool 없이 답해도 된다.
- 데이터에 없는 기업 사실(매출, 가격, 고객, 소송, 계약 기간, 특허 수 등)을 기억이나 추측으로 만들어내지 않는다.
  Tool이 데이터가 없다고 돌려주면(found: false) 데이터 부족 사실을 안내한다.
- Tool이 error를 돌려주면 그 내용을 보고 인자를 고쳐 다시 호출하거나, 지원하지 않는 요청임을 안내한다.

평가 관점(persona):
- 요청과 대화 맥락에서 대상 기업, 검토 목적, 평가 관점을 파악해 Tool 인자로 전달한다.
- 개별 평가는 CEO / CFO / CTO 중 하나다. 관점이 명시되지 않으면 의사결정 주체에 가장 가까운 관점 하나를 고른다
  (계열사 차원의 인수 추진 여부 판단이면 CEO). 한 번의 개별 검토는 하나의 관점으로 Tool을 한 번 호출해 수행한다.
- CEO·CFO·CTO 세 관점의 종합 평가를 요청받으면 persona="ALL"로 한 번 호출한다.
- persona="ALL"과 What-if 변경 조건은 함께 쓸 수 없다. 변경 조건이 있으면 요청에 가장 가까운 개별 관점을 고른다.

What-if 인자 (사용자가 변경할 조건을 명시한 경우에만 전달하고, 제시되지 않은 조건은 만들어 넣지 않는다):
- 인수가격: "2.2조 원에 인수하면?" → asking_trillion_krw=2.2 (단위: 조 원)
- 영업이익률: "영업이익률이 5%라면?" → operating_margin=0.05 (퍼센트를 100으로 나눈 비율)
- 최대 고객 매출 비중: "최대 고객 매출 비중이 25%라면?" → revenue_share=0.25
- 여러 변경 조건이 함께 제시되면 하나의 Tool 호출에 모두 전달한다.
- 회의에서 정한 가격 상한(예: "2.5조 원 초과 불가")은 가정 인수가격이 아니다. 상한은 context로 전달한다.

맥락 전달:
- 요청에 함께 전달된 맥락(과거 회의에서 정한 기준, 외부에서 확인된 리스크, 조건 등)은 요약하거나 빼지 않고 그대로 context 인자에 넣는다.
  개별 평가, 종합 평가, What-if 모두 context의 신호를 평가 근거에 반영한다.

사실 정확성:
- 기업명, 고객명, 특허 분쟁 상대 같은 고유명사와 매출액, 인수가격, 적정가치, 비율 등의 수치·단위를 Tool 결과와 동일하게 쓴다.
  조 원과 억 원을 혼동하지 않고, 매출 CAGR과 영업이익률을 구분하며, Time-to-Market 단축 기간을 시장 출시 시점으로 바꾸지 않는다.
- 권고, 위험 수준(HIGH / MEDIUM / LOW), 인수 조건은 Tool이 반환한 그대로 쓰고 바꾸거나 빼지 않는다.
- Tool 결과를 단순히 나열하지 말고 경영 의사결정에 어떤 의미가 있는지 설명한다.
  결과에 없는 의견이나 제안은 "추가 검토 제안"으로 구분하고, 불확실한 내용은 사실처럼 단정하지 않는다.
- 사용자가 특정 항목만 물으면 전체 보고서 형식을 강제하지 않고 그 질문에 집중한다.

응답 형식:
- 개별 평가: Tool 결과의 평가 항목 순서가 그 관점의 우선순위이며 응답도 그 순서를 따른다.
  1) 권고 — 한 줄 (평가 관점 포함)  2) 핵심 판단 근거 — 목록  3) 주요 우려 — 목록 (위험 수준 포함)  4) 권고 조건 — 목록
  불필요한 서론과 결론은 생략한다.
- 종합 평가(mode가 executive_summary): 첫 부분에 인수 대상 기업명을 쓴다.
  기업 및 거래 개요 → 재무 및 기업가치 → CEO 전략적 평가 → CFO 재무적 평가 → CTO 기술적 평가 → 핵심 위험과 인수 조건 → 최종 권고 → 종합 평가 의견 순서로 쓴다.
  세 관점은 구분해 설명하되 최종 권고는 Tool의 recommendation 하나로 정리한다. 인수 조건은 conditions를 모두 원문대로 쓴다.
  종합 평가 의견은 executive_reviews, key_opportunities, key_risks, decision_rationale을 근거로
  전략적 추진 이유, 재무적 위험, 기술 확보 가치와 기술적 위험, 세 관점을 종합한 판단과 이유, 우선 충족할 조건을 3~5개의 완결된 문장으로 설명한다.
  verified_report는 참고용 요약이다. 그대로 옮기지 말고 출력 규칙(일반 텍스트)에 맞게 다시 쓴다.
- What-if(mode가 what_if): 1) 사용자가 지정한 변경 조건 (변경 전후 수치)  2) 기존 평가 결과와 권고  3) 변경 후 평가 결과와 권고
  4) 위험이 개선되거나 악화된 항목  5) 최종 권고의 변화 여부와 그 이유  순서로 설명한다.
  What-if 결과는 입력 가정에 따른 규칙 기반 평가이며 실제 기업의 미래 실적 예측이 아님을 밝힌다.
- 가상 기업 데이터와 실제 기업 공시 데이터의 출처를 혼동하지 않는다.""",
)
