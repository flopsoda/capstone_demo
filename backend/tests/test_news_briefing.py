"""News briefing workflow (app/library/workflows/news_briefing.py): one request runs
collect → normalize → filter → analyze → respond. No network, no LLM key: fake sources and a fake model."""

import json
from datetime import date

from app.library.workflows.news_briefing import Article, FixtureSource, run_news_briefing
from app.systems.k_electronics import DATA_DIR

TODAY = date(2026, 11, 10)


def art(id_, title, summary, outlet, day, url):
    return Article(id_, title, summary, url, outlet, day, "test", content_level="snippet")


ARTICLES = [
    art("a1", "HBM 시장, 내년 공급 부족 전망…가격 상승", "HBM 시장 공급 부족 전망.", "테크신문", "2026-11-09", "https://n.example/1"),
    art("a2", "HBM 시장 내년 공급 부족 전망, 가격 오른다", "HBM 공급 부족.", "경제일보", "2026-11-09", "https://n.example/2"),
    art("a2b", "HBM 시장 내년 공급 부족 전망, 가격 오른다", "같은 기사 중복.", "경제일보", "2026-11-09", "https://n.example/2"),
    art("a3", "美 HBM 수출 통제 강화 검토", "수출 통제 강화가 검토된다.", "산업일보", "2026-11-04", "https://n.example/3"),
]


class ListSource:
    name = "list"

    def __init__(self, articles=ARTICLES):
        self.articles = articles

    def search(self, query, since):
        return [a for a in self.articles if date.fromisoformat(a.date) >= since]


def run(query="HBM 시장", **kw):
    kw.setdefault("sources", [ListSource()])
    return run_news_briefing(query, today=TODAY, **kw)


def test_one_request_runs_every_stage_in_order():
    stages = []
    out = run(log=lambda stage, detail: stages.append(stage))
    assert stages == ["collect", "filter", "analyze", "respond"]
    assert out["collected"] == 4 and out["duplicates_removed"] == 1


def test_any_query_works_not_only_registered_companies():
    out = run("HBM 시장")  # an industry: not in companies.json
    assert out["topic_count"] == 2


def test_other_outlets_reports_of_one_story_become_one_topic_ranked_first():
    top = run()["topics"][0]
    assert top["article_count"] == 2 and top["outlet_count"] == 2
    assert "공급 부족" in top["articles"][0]["title"]
    assert top["importance"] > run()["topics"][1]["importance"]


def test_without_an_llm_the_rules_still_answer():
    out = run()
    assert out["analysis"] == "rule" and out["trend"] == "" and out["so_what"] == ""
    assert all(t["summary"] and t["key_facts"] for t in out["topics"])


def test_llm_fills_summaries_trend_and_so_what():
    def llm(system, user):
        topics = json.loads(user.split("주제별 기사:\n", 1)[1])
        return "```json\n" + json.dumps({
            "topics": [{"id": t["id"], "summary": f"요약 {t['id']}", "key_facts": ["사실"], "sentiment": "negative",
                        "importance": "high"} for t in topics],
            "trend": "공급 부족과 규제가 겹친다.", "so_what": "조달 계획을 점검해야 한다.",
        }, ensure_ascii=False) + "\n```"

    out = run(llm=llm)
    assert out["analysis"] == "llm" and out["trend"] and out["so_what"]
    assert out["topics"][0]["summary"] == "요약 topic-1" and out["topics"][0]["sentiment"] == "negative"
    assert out["negative_alerts"] == ["topic-1"]  # important and negative


def test_a_broken_llm_falls_back_to_the_rules():
    def broken(system, user):
        raise TimeoutError

    assert run(llm=broken)["analysis"] == "rule"
    assert run(llm=lambda s, u: "모르겠습니다")["analysis"] == "rule"


def test_risk_purpose_adds_risk_labels_and_check_points():
    general = run()
    risk = run(purpose="risk")
    assert "risk" not in general["topics"][0]
    export = next(t for t in risk["topics"] if "수출 통제" in t["articles"][0]["title"])
    assert export["risk"]["category"] == "regulatory" and export["check_points"]


def test_unknown_purpose_is_general():
    assert run(purpose="m&a")["purpose"] == "general"


def test_a_failing_source_does_not_stop_the_others():
    class Down:
        name = "down"

        def search(self, query, since):
            raise OSError

    out = run(sources=[Down(), ListSource()])
    assert out["failed_sources"] == ["down"] and out["collected"] == 4


def test_demo_scenario_runs_through_the_same_workflow():
    out = run_news_briefing("뉴로엣지", purpose="risk", days=30, sources=[FixtureSource(DATA_DIR / "news.json")], today=date(2026, 10, 10))
    labels = sorted((t["risk"]["label"], t["article_count"]) for t in out["topics"] if t.get("risk"))
    assert ("특허 소송", 2) in labels  # the lawsuit and the injunction: one matter
    assert any(t["sentiment"] == "positive" for t in out["topics"])  # the appliance-maker deal


def test_empty_query():
    assert "error" in run_news_briefing(" ", sources=[])


def test_the_fictional_scenario_is_scored_from_its_own_last_day():
    fixture = FixtureSource(DATA_DIR / "news.json")
    assert fixture.latest() == date(2026, 10, 8)
    out = run_news_briefing("뉴로엣지", purpose="risk", days=30, sources=[fixture], today=fixture.latest())
    assert out["topics"][0]["importance"] >= 50 and out["negative_alerts"]


def test_content_adjusts_the_filter_score():
    from app.library.workflows.news_briefing import adjusted_importance

    base = {"importance": 40}
    assert adjusted_importance({**base, "risk": {"level": "HIGH"}}) == 55
    assert adjusted_importance({**base, "importance_llm": "high"}) == 55
    assert adjusted_importance({**base, "importance_llm": "low", "risk": {"level": "LOW"}}) == 30


def test_the_agent_tool_runs_the_workflow_not_itself(monkeypatch):
    # The Tool and the workflow share the name run_news_briefing; the Tool once called itself.
    from app.library.tools import run_news_briefing as tool_module
    from app.systems.k_electronics.tools.run_news_briefing import run_news_briefing as tool

    monkeypatch.setattr(tool_module, "_llm", lambda: None)
    out = tool.invoke({"query": "뉴로엣지", "purpose": "risk", "days": 30})
    assert out["topic_count"] >= 1 and out["purpose"] == "risk"


def test_demo_mode_is_the_fixture_on_its_own_calendar(monkeypatch):
    from app.library.tools import run_news_briefing as tool_module
    from app.systems.k_electronics.tools.run_news_briefing import run_news_briefing as tool

    monkeypatch.setattr(tool_module, "_llm", lambda: None)
    out = tool.invoke({"query": "뉴로엣지", "days": 7})  # the scenario ends 2026-10-08
    assert out["dataset"] == "demo" and out["period"] == {"from": "2026-10-01", "to": "2026-10-08"}
    dates = [a["date"] for t in out["topics"] for a in t["articles"]]
    assert dates and min(dates) >= "2026-10-01"  # the window applies to the fixture too


def test_live_mode_never_serves_fictional_articles(monkeypatch):
    from app.library.tools import run_news_briefing as tool_module
    from app.systems.k_electronics.tools.run_news_briefing import run_news_briefing as tool

    monkeypatch.setenv("NEWS_DATA_MODE", "live")
    monkeypatch.setattr(tool_module, "_llm", lambda: None)
    out = tool.invoke({"query": "뉴로엣지", "days": 30})  # no news database configured in this test
    assert out["dataset"] == "live" and out["topics"] == [] and "저장소" in out["error"]
