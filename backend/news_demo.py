"""뉴스 Agent 시연 (설치 없이 Python만으로 실행).

    python news_demo.py              발표 시나리오(뉴로엣지)만
    python news_demo.py 삼성전자     + 실제 뉴스를 모아서 분석

LLM 없이 Tool이 만드는 결과(뉴스 비서가 읽는 자료)를 그대로 보여 줍니다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.library.workflows.company_risk_news_analysis import analyze_company_risk_news  # noqa: E402
from app.library.workflows.news_collection import COLLECTED_FILE, collect  # noqa: E402

DATA = Path(__file__).resolve().parent / "app" / "systems" / "k_electronics" / "data"
LEVEL = {"HIGH": "높음", "MEDIUM": "중간", "LOW": "낮음"}


def show(out: dict) -> None:
    if not out["risks"]:
        print("  찾은 위험 없음")
    for i, r in enumerate(out["risks"], 1):
        print(f"\n  {i}. [{r['label']} · 위험 {LEVEL[r['level']]}] {r['headline']}")
        print(f"     기사 {r['report_count']}건 · 매체 {r['outlet_count']}곳 ({r['first_reported']} ~ {r['last_reported']}) · 흐름: {r['trend']}")
        if r.get("escalated_by_coverage"):
            print("     (여러 매체가 보도해서 위험 수준을 한 단계 올렸어요)")
        for f in r["key_facts"][:3]:
            print(f"       - {f}")
        print(f"     다음에 확인할 것: {r['check_points'][0]}")
        if r["source"].get("url"):
            print(f"     원문: {r['source']['name']} 기사")
    tones = [t for t in out.get("outlet_tone", []) if t["tone"] != "판단 보류"]
    if tones:
        print("\n  [매체별 논조]")
        for t in tones:
            print(f"   - {t['outlet']}: {t['tone']} (부정 {t['negative']} · 긍정 {t['positive']} · 중립 {t['neutral']})")
    for p in out["positive_signals"]:
        print(f"\n  (좋은 소식 · 매체 {p.get('outlet_count', 1)}곳) {p['headline']} · {p['source']['date']}")


def line(title: str) -> None:
    print("\n" + "=" * 70 + f"\n {title}\n" + "=" * 70)


line("시연 1. 사장님: \"뉴로엣지 인수할 때 조심할 뉴스 있어?\" (발표 시나리오, 가상 기사)")
show(analyze_company_risk_news("뉴로엣지 인수 판단 외부 리스크", "뉴로엣지", ["주요 고객", "법적 분쟁", "핵심인력"], path=DATA / "news.json"))

for company in sys.argv[1:]:
    line(f"시연 2. 실제 뉴스 모으기: {company} (최근 7일, 단어 규칙으로 분류)")
    collect([(company, [])], DATA / COLLECTED_FILE, days=7, log=lambda s: print("  " + s))
    line(f"시연 3. 질문: \"{company} 최근 리스크 알려줘\"")
    show(analyze_company_risk_news(f"{company} 최근 리스크", company, path=DATA / "news.json", collected=DATA / COLLECTED_FILE))
print()
