"""news_briefing — how the News Agent turns the news_briefing workflow's result into a briefing."""

from __future__ import annotations

from ...runtime.capabilities import Skill
from ..tools.news_personalization import INTERESTS_TOOL, PERSONAL_TOOL
from ..tools.run_news_briefing import TOOL_NAME

NEWS_BRIEFING = Skill(
    name="news_briefing",
    description="요청한 주제의 최근 뉴스를 수집·분석해 경영진용 브리핑으로 정리하는 방법.",
    instructions=f"""\
- 뉴스는 반드시 Workflow Tool({TOOL_NAME})로 가져온다. 기억이나 추측으로 뉴스를 만들어내지 않는다.
- 요청에서 검색할 주제를 1~3단어 핵심어로 뽑아 query로 넘긴다 (예: "삼성전자 최근 이슈 알려줘" → "삼성전자",
  "AI 반도체 수출 규제 관련 위험 있어?" → "AI 반도체 수출 규제"). "리스크", "동향", "논란" 같은 말이나 요청에 없는 기업명은 넣지 않는다.
  리스크 점검·인수 검토처럼 위험을 묻는 요청이면 purpose를 "risk"로, 그 밖에는 "general"로 둔다.
- 개인화: "내 브리핑", "내 관심사 뉴스"처럼 사용자 본인 기준 요청은 {PERSONAL_TOOL}를 쓴다. 텔레그램·이메일로
  보내 달라고 할 때만 send=true. 결과의 delivery에 sent/failed/not_configured가 있으면 어디로 보냈는지(또는 왜 못 보냈는지) 한 줄로 알린다.
  관심 주제를 추가·삭제하거나 빼고 싶은 단어, 브리핑 주기(daily/weekly)를 바꾸는 요청은 {INTERESTS_TOOL}로 처리하고 바뀐 설정을 짧게 확인해 준다.
  개인 브리핑의 topics에는 interest(어느 관심 주제에서 나왔는지)가 있다. 주요 이슈 앞에 (관심 주제)를 붙인다.
- Tool 결과에 없는 내용은 답하지 않는다. topics가 비어 있으면 해당 기간 기사가 없다고 말한다.
- 최종 응답 형식 (순서 고정):
  1) 한눈에 보기 — trend가 있으면 그대로 요약해 2~3문장. 없으면 상위 주제로 한두 문장.
  2) 주요 이슈 — 중요도(importance) 높은 순으로 주제마다 한 단락: 무슨 일인지(summary), 근거 사실(key_facts, 날짜 포함),
     긍정/부정, 보도 매체 수(outlet_count). purpose가 "risk"면 리스크 종류·수준과 check_points 중 관련 있는 확인 사항.
  3) 시사점(So-What) — so_what이 있으면 그 내용을, 없으면 주요 이슈에서 근거가 분명한 것만 1~2문장.
  4) 알림 — negative_alerts에 든 주제가 있으면 "부정 기사 알림"으로 한 줄씩.
  5) 매체별 논조 — outlet_tone에서 "판단 보류"가 아닌 매체만, 있을 때만 한 줄.
- 불필요한 서론과 결론은 생략한다.""",
)
