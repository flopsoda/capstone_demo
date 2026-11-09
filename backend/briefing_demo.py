"""뉴스 브리핑 파이프라인 시연 + LLM 모델 비교 (설치 없이 Python만으로 실행).

    python briefing_demo.py "HBM 시장"                  한 모델(OLLAMA_MODEL)로 브리핑
    python briefing_demo.py 뉴로엣지 --purpose risk      리스크 관점
    python briefing_demo.py "HBM 시장" --compare gpt-oss:20b-cloud,gemma4:31b-cloud
                                                         같은 기사로 모델별 분석 · 속도 · Tool 호출 정확도 비교
    python briefing_demo.py "HBM 시장" --no-llm          규칙만

키는 코드에 넣지 않습니다. 환경변수 또는 backend/.env의 OLLAMA_API_KEY · OLLAMA_MODEL · OLLAMA_BASE_URL을 읽습니다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.library.workflows.news_briefing import FixtureSource, GoogleNewsSource, run_news_briefing, topic_line  # noqa: E402

FIXTURE = HERE / "app" / "systems" / "k_electronics" / "data" / "news.json"
STAGE = {"collect": "1. 수집", "filter": "2. 필터링", "analyze": "3. 분석", "respond": "4. 응답"}


def load_env() -> None:
    env = HERE / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#") and value.strip() and not os.environ.get(key.strip()):
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def _post(path: str, body: dict, timeout: int = 120) -> dict:
    base = (os.environ.get("OLLAMA_BASE_URL") or "https://ollama.com").rstrip("/")
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["OLLAMA_API_KEY"]},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def resolve_model(model: str) -> str:
    """Ollama Cloud API는 보통 '-cloud' 없는 이름을 쓴다. 그대로 안 되면 붙은 꼬리를 떼고 한 번 더 시도."""
    for name in dict.fromkeys([model, model.removesuffix("-cloud")]):
        try:
            _post("/api/chat", {"model": name, "stream": False, "messages": [{"role": "user", "content": "ok"}]}, 60)
            return name
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} {e.read()[:200].decode(errors='replace')}"
        except Exception as e:  # noqa: BLE001
            last = repr(e)
    raise RuntimeError(f"모델 {model} 호출 실패: {last}")


def ollama_llm(model: str, stats: dict):
    def call(system: str, user: str) -> str:
        t = time.monotonic()
        try:
            out = _post("/api/chat", {"model": model, "stream": False, "options": {"temperature": 0.2},
                                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        except Exception as e:  # noqa: BLE001
            stats["error"] = repr(e)[:200]
            raise
        finally:
            stats["seconds"] = round(time.monotonic() - t, 1)
        stats["raw"] = out["message"]["content"]
        return stats["raw"]

    return call


# --- Tool 호출 정확도: News Agent가 받을 법한 요청 → 모델이 run_news_briefing을 맞는 인자로 부르는지 ---
TOOL = {"type": "function", "function": {
    "name": "run_news_briefing",
    "description": "요청한 주제(기업, 산업, 시장 동향, 규제, 인물 등)의 최근 뉴스를 수집·분석해 브리핑 자료를 반환한다.",
    "parameters": {"type": "object", "required": ["query"], "properties": {
        "query": {"type": "string", "description": "검색할 주제 (예: 삼성전자, HBM 시장)"},
        "purpose": {"type": "string", "enum": ["general", "risk"], "description": "리스크 점검·인수 검토면 risk, 그 밖에는 general"},
        "days": {"type": "integer", "description": "며칠 전 기사까지 (기본 7)"},
    }},
}}
TOOL_CASES = [  # (요청, query에 들어가야 할 말, 기대 purpose)
    ("삼성전자 요즘 뉴스 정리해줘", "삼성전자", "general"),
    ("뉴로엣지 인수 검토 중인데 악재 있는지 봐줘", "뉴로엣지", "risk"),
    ("HBM 시장 이번 달 동향 알려줘", "HBM", "general"),
    ("AI 반도체 수출 규제 관련해서 우리한테 위험한 거 있어?", "수출", "risk"),
]
AGENT_SYSTEM = "너는 K전자의 뉴스 에이전트다. 뉴스는 반드시 run_news_briefing Tool로 가져온다."


def tool_call_check(model: str) -> tuple[int, list[str]]:
    hits, notes = 0, []
    for request, word, purpose in TOOL_CASES:
        try:
            out = _post("/api/chat", {"model": model, "stream": False, "tools": [TOOL], "messages": [
                {"role": "system", "content": AGENT_SYSTEM}, {"role": "user", "content": request}]})
            calls = out["message"].get("tool_calls") or []
            args = calls[0]["function"]["arguments"] if calls else {}
            args = json.loads(args) if isinstance(args, str) else args
            ok = bool(calls) and calls[0]["function"]["name"] == "run_news_briefing" and word in str(args.get("query", "")) \
                and args.get("purpose", "general") == purpose
            notes.append(f"{'O' if ok else 'X'} {request} → {json.dumps(args, ensure_ascii=False) if calls else '(Tool 안 부름)'}")
        except Exception as e:  # noqa: BLE001
            ok = False
            notes.append(f"X {request} → 오류 {repr(e)[:80]}")
        hits += ok
    return hits, notes


def show(out: dict, stats: dict | None = None) -> None:
    print(f"\n[{out['query']}] {out['period']['from']} ~ {out['period']['to']} · 기사 {out['collected']}건 수집, 중복 {out['duplicates_removed']}건 제거, 주제 {out['topic_count']}개"
          f" · 분석: {'LLM' if out['analysis'] == 'llm' else '규칙(LLM 없음/실패)'}" + (f" {stats['seconds']}초" if stats and "seconds" in stats else ""))
    if out["failed_sources"]:
        print("  수집 실패:", ", ".join(out["failed_sources"]))
    if out["trend"]:
        print("\n  한눈에 보기:", out["trend"])
    print("\n  주요 이슈")
    for t in out["topics"][:6]:
        print("  " + topic_line(t))
    if out["so_what"]:
        print("\n  시사점(So-What):", out["so_what"])
    for tid in out["negative_alerts"]:
        t = next(x for x in out["topics"] if x["id"] == tid)
        print(f"  ⚠ 부정 기사 알림: {t['articles'][0]['title']}")
    tones = [r for r in out["outlet_tone"] if r["tone"] != "판단 보류"]
    if tones:
        print("  매체별 논조:", ", ".join(f"{r['outlet']} {r['tone']}" for r in tones))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("query", nargs="?", default="뉴로엣지")
    p.add_argument("--purpose", default="general", choices=["general", "risk"])
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--compare", help="쉼표로 구분한 모델 이름들")
    p.add_argument("--no-llm", action="store_true")
    a = p.parse_args()
    load_env()

    sources = [GoogleNewsSource(), FixtureSource(FIXTURE)]
    log = lambda stage, detail: print(f"  {STAGE.get(stage, stage)}: {detail}")  # noqa: E731
    if a.no_llm or not os.environ.get("OLLAMA_API_KEY"):
        if not a.no_llm:
            print("OLLAMA_API_KEY가 없어 규칙으로만 분석합니다 (backend/.env에 넣으면 LLM 분석).")
        show(run_news_briefing(a.query, purpose=a.purpose, days=a.days, sources=sources, log=log))
        return

    models = [m.strip() for m in (a.compare or os.environ.get("OLLAMA_MODEL", "gpt-oss:20b-cloud")).split(",") if m.strip()]
    # 같은 기사로 비교하려고 한 번만 수집한다.
    articles = [x for s in sources for x in _safe(s, a.query, a.days)]
    fixed = [_Fixed(articles)]
    summary = []
    for model in models:
        print(f"\n===== 모델 {model} =====")
        try:
            name = resolve_model(model)
        except RuntimeError as e:
            print(" ", e)
            summary.append((model, "호출 실패", "-", "-"))
            continue
        if name != model:
            print(f"  (API에서는 '{name}' 이름으로 호출됨)")
        stats: dict = {}
        out = run_news_briefing(a.query, purpose=a.purpose, days=a.days, sources=fixed, llm=ollama_llm(name, stats), log=log)
        show(out, stats)
        if out["analysis"] != "llm" and "raw" in stats:
            print("  (모델 답을 JSON으로 읽지 못함. 앞부분:", stats["raw"][:150].replace("\n", " "), ")")
        hits, notes = tool_call_check(name)
        print(f"\n  Tool 호출 정확도 {hits}/{len(TOOL_CASES)}")
        for n in notes:
            print("   ", n)
        summary.append((model, "성공" if out["analysis"] == "llm" else "JSON 실패", f"{stats.get('seconds', '-')}초", f"{hits}/{len(TOOL_CASES)}"))

    print("\n===== 비교 요약 =====")
    print("  모델 | 분석 결과 | 분석 시간 | Tool 호출")
    for row in summary:
        print("  " + " | ".join(row))


def _safe(source, query, days):
    from datetime import date, timedelta
    try:
        return source.search(query, date.today() - timedelta(days=days))
    except Exception as e:  # noqa: BLE001
        print(f"  {source.name} 수집 실패: {e!r}"[:120])
        return []


class _Fixed:
    name = "cached"

    def __init__(self, articles):
        self.articles = articles

    def search(self, query, since):
        return list(self.articles)


if __name__ == "__main__":
    main()
