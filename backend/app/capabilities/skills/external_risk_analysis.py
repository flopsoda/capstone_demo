"""external_risk_analysis — how the News Agent investigates and reports a company's external risks."""

from __future__ import annotations

from ..base import Skill
from ..tools.run_company_risk_news_analysis import run_company_risk_news_analysis

EXTERNAL_RISK_ANALYSIS = Skill(
    name="external_risk_analysis",
    description="외부 뉴스를 바탕으로 특정 기업의 리스크를 조사하고 의사결정에 맞게 정리하는 방법.",
    instructions=f"""\
- 외부 리스크는 반드시 등록된 Workflow Tool({run_company_risk_news_analysis.name})로 조회한다. 기억이나 추측으로 뉴스를 만들어내지 않는다.
- 요청에서 조사 대상 기업과 우선 확인할 리스크 주제(예: 주요 고객, 법적 분쟁, 핵심인력)를 파악해 Tool 인자로 전달한다.
- Tool 결과에 없는 내용은 답하지 않는다. 관련 기사가 없으면 그렇게 말한다. 리스크 수준은 Tool이 반환한 값을 그대로 쓰고 임의로 바꾸지 않는다.
- 최종 응답 형식 (순서 고정):
  1) 리스크 항목별로 한 단락씩 — 리스크 종류와 수준(높음/중간/낮음), 근거가 되는 사실(기사 날짜 포함)
  2) 현재 의사결정에서 확인이 필요한 사항 — 목록
  수준이 높은 리스크부터 적고, 불필요한 서론과 결론은 생략한다.""",
)
