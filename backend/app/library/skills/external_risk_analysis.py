"""external_risk_analysis — how the News Agent investigates and reports a company's external risks."""

from __future__ import annotations

from ...runtime.capabilities import Skill
from ..tools.run_company_risk_news_analysis import TOOL_NAME

EXTERNAL_RISK_ANALYSIS = Skill(
    name="external_risk_analysis",
    description="외부 뉴스를 바탕으로 특정 기업의 리스크를 조사하고 의사결정에 맞게 정리하는 방법.",
    instructions=f"""\
- 외부 리스크는 반드시 등록된 Workflow Tool({TOOL_NAME})로 조회한다. 기억이나 추측으로 뉴스를 만들어내지 않는다.
- 요청에서 조사 대상 기업과 우선 확인할 리스크 주제(예: 주요 고객, 법적 분쟁, 핵심인력)를 파악해 Tool 인자로 전달한다.
- Tool 결과에 없는 내용은 답하지 않는다. 관련 기사가 없으면 그렇게 말한다. 리스크 수준은 Tool이 반환한 값을 그대로 쓰고 임의로 바꾸지 않는다.
- 한 리스크에 후속 보도가 여러 건이면(report_count > 1) timeline의 흐름과 trend(확대/완화/지속)를 한 문장으로 밝힌다.
- 여러 매체가 보도한 리스크(outlet_count)는 몇 곳이 보도했는지 밝힌다. escalated_by_coverage가 true면 "여러 매체 보도로 수준을 한 단계 올렸다"고 함께 적는다.
- 확인이 필요한 사항은 각 리스크의 check_points 중 이번 요청과 관련된 것을 골라, 근거 사실에 맞게 구체적으로 고쳐 쓴다.
- outlet_tone(매체마다 그 매체 기사들의 보도 관점을 모은 것)에 비우호 매체가 있으면 "매체별 논조"로 한 줄 덧붙인다 (예: 비우호 — A신문 부정 3건). 기사 수가 1건뿐인 매체로 단정하지 않는다.
- coverage.status가 "low"면 실제 뉴스 수집량이 적어 결과가 제한적이라고 한 줄 밝힌다. error가 있으면 그 내용을 그대로 알린다.
- positive_signals가 있으면 리스크와 함께 고려할 우호적 보도로 마지막에 한 줄씩 덧붙인다. 리스크 수준은 그대로 둔다.
- 최종 응답 형식 (순서 고정):
  1) 리스크 항목별로 한 단락씩 — 리스크 종류와 수준(높음/중간/낮음), 근거가 되는 사실(기사 날짜 포함), 보도 흐름
  2) 현재 의사결정에서 확인이 필요한 사항 — 목록
  3) 우호적 보도 — positive_signals가 있을 때만
  수준이 높은 리스크부터 적고, 불필요한 서론과 결론은 생략한다.""",
)
