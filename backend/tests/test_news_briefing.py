"""News briefing view (app/library/workflows/news_briefing.py): events → importance → per-article and
per-event analysis → result. Unit tests give brief() articles directly (no store, no network, a fake
model); the scenario tests run the Tool over a system's seeded news store."""

import json
from datetime import date, timedelta

from app.library.workflows.news import cluster
from app.library.workflows.news.text import terms as query_terms
from app.library.workflows.news_briefing import Article, brief, run_news_briefing

TODAY = date(2026, 11, 10)


def art(id_, title, summary, outlet, day, url):
    return Article(id_, title, summary, url, outlet, day, content_level="snippet")


ARTICLES = [
    art("a1", "HBM 시장, 내년 공급 부족 전망…가격 상승", "HBM 시장 공급 부족 전망.", "테크신문", "2026-11-09", "https://n.example/1"),
    art("a2", "HBM 시장 내년 공급 부족 전망, 가격 오른다", "HBM 공급 부족.", "경제일보", "2026-11-09", "https://n.example/2"),
    art("a2b", "HBM 시장 내년 공급 부족 전망, 가격 오른다", "같은 기사 중복.", "경제일보", "2026-11-09", "https://n.example/2"),
    art("a3", "美 HBM 수출 통제 강화 검토", "수출 통제 강화가 검토된다.", "산업일보", "2026-11-04", "https://n.example/3"),
]


def run(query="HBM 시장", articles=ARTICLES, *, days=7, purpose="general", llm=None, log=None):
    """What the pipeline hands brief(): articles in the window, duplicates out, events assigned."""
    since = TODAY - timedelta(days=days)
    found = [a for a in articles if date.fromisoformat(a.date) >= since]
    kept, dropped = cluster.dedupe([a.item() for a in found])
    keep = {i.id for i in kept}
    out = brief([a for a in found if a.id in keep], cluster.assign(kept, query_terms(query)), query, purpose=purpose,
                today=TODAY, since=since, limit=8, llm=llm, log=log or (lambda stage, detail: None))
    return {**out, "duplicates_removed": len(dropped)}


def test_brief_filters_then_analyzes():
    stages = []
    out = run(log=lambda stage, detail: stages.append(stage))
    assert stages == ["filter", "analyze"]
    assert out["collected"] == 3 and out["duplicates_removed"] == 1


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


def fake_llm(stances=None, event=None, calls=None):
    """Answers the article prompt (a JSON array) and the event prompt (a JSON object) like a model would."""
    stances = stances or {}

    def llm(system, user):
        if calls is not None:
            calls.append("article" if "\n\n기사:\n" in user else "event")
        if "\n\n기사:\n" in user:
            arts = json.loads(user.split("\n\n기사:\n", 1)[1])
            return json.dumps([{"id": a["id"], "stance": stances.get(a["id"], "neutral"), "article_type": "report",
                                "risk": None, "key_facts": [f"{a['title']} 사실"], "entities": ["가온"]} for a in arts], ensure_ascii=False)
        topics = json.loads(user.split("사건별 기사:\n", 1)[1])
        return "```json\n" + json.dumps({
            "topics": [{"id": t["id"], "summary": f"요약 {t['id']}", "impact": "negative", "importance": "high",
                        "key_facts": [{"text": "사실", "article_ids": [t["articles"][0]["id"], "made-up"]}],
                        "implication": "조달 계획 점검 필요"} for t in topics],
            "trend": "공급 부족과 규제가 겹친다.", "so_what": "조달 계획을 점검해야 한다.", **(event or {}),
        }, ensure_ascii=False) + "\n```"

    return llm


def test_llm_fills_summaries_trend_and_so_what():
    out = run(llm=fake_llm())
    assert out["analysis"] == "llm" and out["trend"] and out["so_what"] and out["article_analysis"] == ["llm"]
    top = out["topics"][0]
    assert top["summary"] == "요약 topic-1" and top["impact"] == "negative" and top["sentiment"] == "negative"
    assert top["evidence_facts"] == [{"text": "사실", "article_ids": [top["articles"][0]["id"]]}]  # unknown ids dropped
    assert top["key_facts"] == ["사실"] and top["implication"] == "조달 계획 점검 필요"
    assert out["negative_alerts"] == ["topic-1"]  # important and negative


def test_outlets_reporting_one_event_differently_keep_their_own_stance():
    body = "가온반도체가 새 공장을 짓는다는 소식에 대해 평가가 엇갈린다. " * 12
    one_event = [
        Article("p1", "가온반도체 신공장 착공, 성장 동력 확보", "", "https://p.example/1", "가상일보", "2026-11-09",
                content_level="full", body=body),
        Article("p2", "가온반도체 신공장 착공, 과잉 투자 논란", "", "https://p.example/2", "모의경제", "2026-11-09",
                content_level="full", body=body.replace("평가", "우려")),
        Article("p3", "가온반도체 신공장 착공 소식", "착공 소식 요약", "https://p.example/3", "제목일보", "2026-11-09",
                content_level="snippet"),
    ]
    out = run("가온반도체", one_event, llm=fake_llm({"p1": "favorable", "p2": "critical", "p3": "favorable"}))
    top = out["topics"][0]
    assert top["article_count"] == 3  # one event…
    stances = {r["outlet"]: r["stance"] for r in top["stance_by_outlet"]}
    assert stances == {"가상일보": "favorable", "모의경제": "critical", "제목일보": "unknown"}  # …reported three ways
    assert top["divergent_coverage"] is True and top["impact"] == "negative"  # the event's impact is a separate answer
    tone = {r["outlet"]: (r["favorable"], r["critical"], r["unknown"]) for r in out["outlet_tone"]}
    assert tone == {"가상일보": (1, 0, 0), "모의경제": (0, 1, 0), "제목일보": (0, 0, 1)}  # per article, never the topic's value


def test_risk_is_independent_of_stance():
    from app.library.workflows.news.analysis import analyze_articles

    def llm(system, user):
        return json.dumps([{"id": "x", "stance": "favorable", "article_type": "report",
                            "risk": {"category": "regulatory", "level": "MEDIUM"}, "key_facts": ["규제 심사"]}])

    r = analyze_articles([{"id": "x", "title": "가온, 수출 호조 속 규제 심사", "outlet": "o", "text": "본문", "basis": "body"}], "가온", llm)["x"]
    assert r["stance"] == "favorable" and r["risk"]["category"] == "regulatory" and r["method"] == "llm"


def test_a_broken_llm_falls_back_to_the_rules():
    def broken(system, user):
        raise TimeoutError

    out = run(llm=broken)
    assert out["analysis"] == "rule" and out["article_analysis"] == ["rule"]
    assert all(a["stance"] == "unknown" for t in out["topics"] for a in t["articles"] if "stance" in a)  # rules never guess a stance
    assert run(llm=lambda s, u: "모르겠습니다")["analysis"] == "rule"


def test_risk_purpose_adds_risk_labels_and_check_points():
    general = run()
    risk = run(purpose="risk")
    assert "risk" not in general["topics"][0]
    export = next(t for t in risk["topics"] if "수출 통제" in t["articles"][0]["title"])
    assert export["risk"]["category"] == "regulatory" and export["check_points"]


def test_unknown_purpose_is_general():
    assert run(purpose="m&a")["purpose"] == "general"


def test_the_scenario_runs_through_the_same_pipeline(news_db, monkeypatch):
    from app.library.tools import run_news_briefing as tool_module
    from app.systems.k_electronics.tools.run_news_briefing import run_news_briefing as tool

    monkeypatch.setattr(tool_module, "_llm", lambda: None)
    out = tool.invoke({"query": "뉴로엣지", "purpose": "risk"})  # the registered articles, from the store
    labels = sorted((t["risk"]["label"], t["article_count"]) for t in out["topics"] if t.get("risk"))
    assert ("법적 분쟁", 2) in labels  # the lawsuit and the injunction: one stored event
    assert any(t["sentiment"] == "positive" for t in out["topics"])  # the appliance-maker deal
    assert out["article_analysis"] == ["imported"]  # the analyses registered with the articles, reused
    assert all("url" not in a for t in out["topics"] for a in t["articles"])  # registered without a link


def test_empty_query():
    assert "error" in run_news_briefing(" ", store=None, feeds=[], fetcher=None)


def test_content_adjusts_the_filter_score():
    from app.library.workflows.news_briefing import adjusted_importance

    base = {"importance": 40}
    assert adjusted_importance({**base, "risk": {"level": "HIGH"}}) == 55
    assert adjusted_importance({**base, "importance_llm": "high"}) == 55
    assert adjusted_importance({**base, "importance_llm": "low", "risk": {"level": "LOW"}}) == 30


def test_the_agent_tool_runs_the_workflow_not_itself(news_db, monkeypatch):
    # The Tool and the workflow share the name run_news_briefing; the Tool once called itself.
    from app.library.tools import run_news_briefing as tool_module
    from app.systems.k_electronics.tools.run_news_briefing import run_news_briefing as tool

    monkeypatch.setattr(tool_module, "_llm", lambda: None)
    out = tool.invoke({"query": "뉴로엣지", "purpose": "risk", "days": 30})
    assert out["purpose"] == "risk" and "topics" in out


def test_days_narrows_the_window_and_the_default_is_the_lookback(news_db, monkeypatch):
    from app.library.tools import run_news_briefing as tool_module
    from app.library.workflows.news.pipeline import LOOKBACK_DAYS
    from app.systems.k_electronics.tools.run_news_briefing import run_news_briefing as tool

    monkeypatch.setattr(tool_module, "_llm", lambda: None)
    out = tool.invoke({"query": "뉴로엣지"})
    span = date.fromisoformat(out["period"]["to"]) - date.fromisoformat(out["period"]["from"])
    assert span.days == LOOKBACK_DAYS
    recent = tool.invoke({"query": "뉴로엣지", "days": 1})
    assert all(a["date"] >= recent["period"]["from"] for t in recent["topics"] for a in t["articles"])
    monkeypatch.setenv("NEWS_LOOKBACK_DAYS", "10")
    capped = tool.invoke({"query": "뉴로엣지", "days": 365})
    assert (date.fromisoformat(capped["period"]["to"]) - date.fromisoformat(capped["period"]["from"])).days == 10


def test_without_a_news_database_the_tool_answers_with_an_error(monkeypatch):
    from app.library.tools import run_news_briefing as tool_module
    from app.systems.k_electronics.tools.run_news_briefing import run_news_briefing as tool

    monkeypatch.setattr(tool_module, "_llm", lambda: None)
    out = tool.invoke({"query": "뉴로엣지", "days": 30})  # no news database configured in this test
    assert out["topics"] == [] and "설정되지 않았습니다" in out["error"]
