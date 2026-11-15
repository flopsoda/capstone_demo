"""News briefing workflow (app/library/workflows/news_briefing.py): one request runs
collect → normalize → filter → analyze → respond. No network, no LLM key: fake sources and a fake model."""

import json
from datetime import date

from app.library.workflows.news_briefing import FixtureSource, GoogleNewsSource, run_news_briefing
from app.systems.k_electronics import DATA_DIR

TODAY = date(2026, 11, 10)

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss><channel>
<item><title>HBM 시장, 내년 공급 부족 전망…가격 상승 - 테크신문</title><link>https://n.example/1</link>
<pubDate>Mon, 09 Nov 2026 01:00:00 GMT</pubDate><description>HBM 시장 공급 부족 전망.</description><source url="x">테크신문</source></item>
<item><title>HBM 시장 내년 공급 부족 전망, 가격 오른다 - 경제일보</title><link>https://n.example/2</link>
<pubDate>Mon, 09 Nov 2026 03:00:00 GMT</pubDate><description>HBM 공급 부족.</description><source url="x">경제일보</source></item>
<item><title>HBM 시장 내년 공급 부족 전망, 가격 오른다 - 경제일보</title><link>https://n.example/2</link>
<pubDate>Mon, 09 Nov 2026 03:00:00 GMT</pubDate><description>같은 기사 중복.</description><source url="x">경제일보</source></item>
<item><title>美 HBM 수출 통제 강화 검토 - 산업일보</title><link>https://n.example/3</link>
<pubDate>Wed, 04 Nov 2026 01:00:00 GMT</pubDate><description>수출 통제 강화가 검토된다.</description><source url="x">산업일보</source></item>
</channel></rss>"""


def google(body=RSS):
    return GoogleNewsSource(fetch=lambda url, headers: body.encode())


def run(query="HBM 시장", **kw):
    kw.setdefault("sources", [google()])
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

    out = run(sources=[Down(), google()])
    assert out["failed_sources"] == ["down"] and out["collected"] == 4


def test_demo_scenario_runs_through_the_same_workflow():
    out = run_news_briefing("뉴로엣지", purpose="risk", sources=[FixtureSource(DATA_DIR / "news.json")], today=date(2026, 10, 10))
    labels = sorted((t["risk"]["label"], t["article_count"]) for t in out["topics"] if t.get("risk"))
    assert ("특허 소송", 2) in labels  # the lawsuit and the injunction: one matter
    assert any(t["sentiment"] == "positive" for t in out["topics"])  # the appliance-maker deal


def test_outlet_tone_needs_two_articles():
    out = run()
    assert {r["outlet"]: r["tone"] for r in out["outlet_tone"]}["산업일보"] == "판단 보류"


def test_empty_query():
    assert "error" in run_news_briefing(" ", sources=[])


def test_a_long_model_made_query_retries_with_its_first_words():
    asked = []

    def fetch(url, headers):
        asked.append(url)
        return (RSS if len(asked) > 1 else "<rss><channel></channel></rss>").encode()

    out = run("HBM 시장 리스크 논란 분쟁", sources=[GoogleNewsSource(fetch=fetch)])
    assert len(asked) == 2 and out["collected"] == 4


def test_the_fictional_scenario_is_scored_from_its_own_last_day():
    out = run_news_briefing("뉴로엣지", purpose="risk", sources=[FixtureSource(DATA_DIR / "news.json")], today=date(2027, 1, 1))
    assert out["topics"][0]["importance"] >= 50 and out["negative_alerts"]  # a month later, still news


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
    monkeypatch.setattr(tool_module.workflow, "GoogleNewsSource", lambda: google("<rss><channel></channel></rss>"))
    out = tool.invoke({"query": "뉴로엣지", "purpose": "risk", "days": 400})
    assert out["topic_count"] >= 1 and out["purpose"] == "risk"


# ── internal context and history ───────────────────────────────────────────

from app.library.workflows.news_briefing import context_from_companies, context_from_meetings  # noqa: E402

MEETING = {"meetings": [{"date": "2026-09-15", "title": "AI 반도체 전략회의",
                         "decisions": [{"ref": "MTG-1#2", "speaker": "CTO", "statement": "핵심 설계팀 유지가 인수 성공의 핵심"}],
                         "open_issues": [],
                         "open_action_items": [{"id": "AI-7", "text": "최대 고객사 의존도 확인", "owner": "전략기획팀", "due": "2026-11-30"}]}]}


def scenario(**kw):
    ctx = context_from_meetings(MEETING) + context_from_companies(DATA_DIR / "companies.json", "뉴로엣지")
    return run_news_briefing("뉴로엣지", purpose="risk", sources=[FixtureSource(DATA_DIR / "news.json")],
                             today=date(2026, 10, 10), context=ctx, **kw)


def test_stories_are_linked_to_our_decisions_and_open_items():
    out = scenario()
    links = {t["articles"][0]["title"][:12]: [l["ref"] for l in t["internal_links"]] for t in out["topics"]}
    cto = next(v for k, v in links.items() if "CTO" in k)
    customer = next(v for k, v in links.items() if "최대 고객사" in k)
    assert "MTG-1#2" in cto and "neuroedge:team" in cto
    assert "AI-7" in customer and "neuroedge:customer" in customer
    assert out["internal_context"] == 6


def test_company_facts_only_for_the_company_asked_about():
    assert context_from_companies(DATA_DIR / "companies.json", "HBM 시장") == []


def test_the_model_can_link_and_explain_but_only_known_records():
    def llm(system, user):
        assert "우리 회사 내부 기록" in user and "internal_links" in system
        topics = json.loads(user.split("주제별 기사:\n", 1)[1])
        return json.dumps({"topics": [{"id": t["id"], "internal_links": ["neuroedge:valuation", "made-up"],
                                       "internal_note": "가격 조건에 영향"} for t in topics], "trend": "", "so_what": "s"})

    t = scenario(llm=llm)["topics"][0]
    assert [l["ref"] for l in t["internal_links"]] == ["neuroedge:valuation"] and t["internal_note"] == "가격 조건에 영향"


def test_a_linked_story_ranks_higher():
    from app.library.workflows.news_briefing import adjusted_importance

    assert adjusted_importance({"importance": 40, "internal_links": [{"ref": "x"}]}) == 50


def test_the_second_look_says_what_is_new_and_what_developed(tmp_path):
    h = tmp_path / "history.json"
    one = RSS.replace("""<item><title>美 HBM""", """<!--<item><title>美 HBM""").replace("산업일보</source></item>", "산업일보</source></item>-->")
    first = run(sources=[google(one)], history=h)
    assert first["since_last"]["previous_run"] is None and first["topics"][0]["tracking"]["status"] == "first_look"
    second = run(history=h)  # the export-control story appears
    since = second["since_last"]
    assert since["previous_run"] and since["new"] == 1 and since["unchanged"] == 1
    new = next(t for t in second["topics"] if "수출 통제" in t["articles"][0]["title"])
    assert new["tracking"]["status"] == "new"
    assert [e["date"] for e in second["topics"][0]["timeline"]] == sorted(e["date"] for e in second["topics"][0]["timeline"])


def test_live_results_that_never_name_the_query_are_dropped():
    rss = RSS.replace("美 HBM 수출 통제 강화 검토", "유럽 냉장고 해킹 확산").replace("수출 통제 강화가 검토된다.", "마트 비상.")
    assert run(sources=[google(rss)])["collected"] == 3
