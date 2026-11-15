"""news_briefing — how the News Agent turns the news_briefing workflow's result into a briefing."""

from __future__ import annotations

from ...runtime.capabilities import Skill
from ..tools.run_company_risk_news_analysis import TOOL_NAME as RISK_TOOL
from ..tools.run_news_briefing import TOOL_NAME

NEWS_BRIEFING = Skill(
    name="news_briefing",
    description="요청한 주제의 최근 뉴스를 수집·분석해 경영진용 브리핑으로 정리하는 방법.",
    instructions=f"""\
- 뉴스는 반드시 Workflow Tool({TOOL_NAME})로 가져온다. 기억이나 추측으로 뉴스를 만들어내지 않는다.
- 동향·브리핑 요청은 {TOOL_NAME}, 특정 기업의 의사결정(인수, 투자, 거래 등)에 영향을 줄 리스크 조사는 {RISK_TOOL}를 쓴다.
- 요청에서 검색할 주제를 1~3단어 핵심어로 뽑아 query로 넘긴다 (예: "삼성전자 최근 이슈 알려줘" → "삼성전자",
  "AI 반도체 수출 규제 관련 위험 있어?" → "AI 반도체 수출 규제"). "리스크", "동향", "논란" 같은 말이나 요청에 없는 기업명은 넣지 않는다.
  동향 브리핑 중에도 위험 요소를 함께 묻는 요청이면 purpose를 "risk"로, 그 밖에는 "general"로 둔다.
- Tool 결과에 없는 내용은 답하지 않는다. topics가 비어 있으면 해당 기간 기사가 없다고 말한다. error가 있으면 그 내용을 그대로 알린다.
- coverage.status가 "low"면 수집된 기사가 적어 결과가 제한적이라고 한 줄로 밝힌다. 기사마다 content_level(full·partial = 본문, snippet = 요약, title = 제목만)이
  있으니, 본문 없이 요약·제목만 있는 기사에 기댄 내용은 단정하지 않는다.
- 최종 응답 형식 (순서 고정):
  1) 한눈에 보기 — trend가 있으면 그대로 요약해 2~3문장. 없으면 상위 주제로 한두 문장.
  2) 주요 이슈 — 중요도(importance) 높은 순으로 주제마다 한 단락: 무슨 일인지(summary), 근거 사실(evidence_facts, 날짜 포함),
     대상에 대한 영향(impact: 긍정/부정/엇갈림/중립), 경영적 의미(implication, 있을 때), 보도 매체 수(independent_outlet_count가 더 작으면
     "독립 보도 n곳"), 근거 기사(매체·날짜). divergent_coverage가 true면 stance_by_outlet으로 "매체별로 보도 관점이 엇갈림
     (A 우호, B 비판)"을 한 줄 덧붙인다. purpose가 "risk"면 리스크 종류·수준과 check_points 중 관련 있는 확인 사항.
  3) 시사점(So-What) — so_what이 있으면 그 내용을, 없으면 주요 이슈에서 근거가 분명한 것만 1~2문장.
  4) 알림 — negative_alerts에 든 주제가 있으면 "부정 기사 알림"으로 한 줄씩.
  5) 매체별 논조 — outlet_tone(기사별 보도 관점을 매체마다 모은 것)에서 "판단 보류"가 아닌 매체가 있을 때만 한 줄.
- 불필요한 서론과 결론은 생략한다.""",
)
