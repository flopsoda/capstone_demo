"""The company risk view (run_company_risk_news_analysis) over each system's news store, seeded from
its data/news.json: the stored events group the reports, the stored analyses label them (no model)."""

import json
from pathlib import Path

import pytest

from app.library.workflows.company_risk_news_analysis import analyze_company_risk_news as risk_view, requested_categories
from app.systems.group.tools.run_company_risk_news_analysis import run_company_risk_news_analysis as group_tool
from app.systems.k_electronics.tools.run_company_risk_news_analysis import run_company_risk_news_analysis as k_tool


@pytest.fixture
def stores(news_db, monkeypatch):
    from app.library.tools import run_news_briefing as briefing_tool

    monkeypatch.setattr(briefing_tool, "_llm", lambda: None)


def analyze_company_risk_news(query, company, topics=None, *, tool=k_tool):
    return tool.invoke({"query": query, "company": company, "topics": topics or []})


def test_returns_three_neuroedge_risks_high_first(stores):
    out = analyze_company_risk_news(
        "뉴로엣지 인수 판단에 영향을 줄 수 있는 최근 외부 리스크를 조사해줘.",
        "뉴로엣지",
        ["주요 고객", "법적 분쟁", "핵심인력"],
    )
    assert out["matched"] == 3
    assert [(r["category"], r["level"]) for r in out["risks"]] == [
        ("customer_concentration", "HIGH"),
        ("key_personnel", "HIGH"),
        ("legal_dispute", "MEDIUM"),
    ]
    customer = out["risks"][0]
    assert "알파모바일" in customer["headline"] and any("46%" in f for f in customer["key_facts"])
    assert customer["source"]["date"] == "2026-10-02"


def test_company_aliases_match_english_and_mixed_forms(stores):
    # The names an article is linked to (news_mentions) find it as well as its text does.
    for name in ("NeuroEdge", "neuroedge"):
        assert analyze_company_risk_news("risks", name)["matched"] == 3  # the stored analysis toward that name
    mixed = analyze_company_risk_news("risks", "뉴로엣지(NeuroEdge)")
    assert mixed["coverage"]["stored_matches"] == 6  # found; analyzed afresh toward "뉴로엣지 neuroedge" (rules here)


def test_topics_narrow_to_requested_categories(stores):
    out = analyze_company_risk_news("뉴로엣지 소송 현황", "뉴로엣지", ["법적 분쟁"])
    assert [r["category"] for r in out["risks"]] == ["legal_dispute"]
    out = analyze_company_risk_news("핵심인력 이탈 가능성", "뉴로엣지")
    assert [r["category"] for r in out["risks"]] == ["key_personnel"]


def test_no_topic_hint_returns_every_risk_of_the_company(stores):
    out = analyze_company_risk_news("최근 악재", "뉴로엣지")
    assert out["matched"] == 3


def test_other_companies_are_filtered_out(stores):
    out = analyze_company_risk_news("최대 고객사 리스크", "뉴로엣지", ["주요 고객"])
    assert [r["source"]["id"] for r in out["risks"]] == ["NEWS-2026-10-02-01"]
    out = analyze_company_risk_news("가동률", "오메가파운드리")
    assert [r["category"] for r in out["risks"]] == ["customer_concentration"]


def test_unknown_company_returns_empty(stores):
    out = analyze_company_risk_news("리스크", "존재하지않는회사")
    assert out["matched"] == 0 and out["risks"] == []


def test_requested_categories_keywords():
    assert requested_categories("주요 고객 관련 리스크, 법적 분쟁, 핵심인력", []) == {
        "customer_concentration",
        "legal_dispute",
        "key_personnel",
    }
    assert requested_categories("최근 악재", []) == set()
    assert requested_categories("규제 이슈랑 주가 반응", []) == {"regulatory", "market_reaction"}


# ── Follow-up reports, favourable news, collected store ────────────────────

def _risk(out, category):
    return next(r for r in out["risks"] if r["category"] == category)


def test_follow_up_reports_of_one_matter_become_one_risk(stores):
    out = analyze_company_risk_news("뉴로엣지 소송 현황", "뉴로엣지", ["법적 분쟁"])
    legal = _risk(out, "legal_dispute")
    assert out["matched"] == 1 and legal["report_count"] == 2
    assert [t["id"] for t in legal["timeline"]] == ["NEWS-2026-09-24-01", "NEWS-2026-10-08-01"]  # oldest first
    assert legal["source"]["id"] == "NEWS-2026-10-08-01"  # lead: same level, newest
    assert legal["first_reported"] == "2026-09-24" and legal["last_reported"] == "2026-10-08"
    assert legal["trend"].startswith("지속")
    # Facts of every report, lead first.
    assert any("가처분" in f for f in legal["key_facts"]) and any("원고: 코어NPU" in f for f in legal["key_facts"])


def test_trend_and_level_of_a_matter_follow_its_reports(stores):
    out = analyze_company_risk_news("핵심인력 이탈", "K바이오", tool=group_tool)
    risk = out["risks"][0]
    assert risk["report_count"] == 2 and risk["level"] == "HIGH"  # the matter's highest level
    # HIGH, then a MEDIUM follow-up from another outlet: labeled reports never read as an easing.
    assert risk["trend"] == "지속 (후속 보도 1건)"
    assert risk["source"]["id"] == "NEWS-G-2026-10-05-01"  # lead: the most severe report


def test_every_risk_carries_check_points_for_its_kind(stores):
    out = analyze_company_risk_news("최근 악재", "뉴로엣지")
    assert all(r["check_points"] for r in out["risks"])
    assert any("판매금지" in c for c in _risk(out, "legal_dispute")["check_points"])


def test_favourable_news_comes_back_apart_from_risks(stores):
    out = analyze_company_risk_news("최근 악재", "뉴로엣지")
    assert out["matched"] == 3  # favourable news is not a risk
    assert [p["source"]["id"] for p in out["positive_signals"]] == ["NEWS-2026-10-06-01"]
    risk_ids = {t["id"] for r in out["risks"] for t in r["timeline"]}
    assert "NEWS-2026-10-06-01" not in risk_ids


def test_follow_ups_merge_for_key_personnel_too(stores):
    people = _risk(analyze_company_risk_news("핵심인력", "뉴로엣지"), "key_personnel")
    assert people["report_count"] == 2
    assert people["source"]["id"] == "NEWS-2026-09-30-01"  # same level (HIGH): the newest leads
    assert any("스톡옵션" in f for f in people["key_facts"])


def test_live_articles_are_read_the_same_way():
    live = [{
        "id": "art_x", "date": "2026-10-20", "source": "example.com", "url": "https://example.com/a",
        "headline": "뉴로엣지 노조 설립 움직임", "summary": "임금 협상 결렬 시 쟁의 가능성.",
        "companies": ["뉴로엣지"], "key_facts": ["노조 설립 움직임"], "labeled_by": "rule",
        "risk": {"category": "labor_dispute", "label": "노사 분쟁", "level": "LOW"},
    }]
    out = risk_view("최근 악재", "뉴로엣지", articles=live)
    assert out["matched"] == 1 and out["risks"][0]["source"]["url"] == "https://example.com/a"


def test_without_a_news_database_the_tool_answers_with_an_error():
    out = k_tool.invoke({"query": "리스크", "company": "뉴로엣지"})  # no database configured in this test
    assert out["matched"] == 0 and out["risks"] == [] and "설정되지 않았습니다" in out["error"]


def test_the_registered_articles_keep_their_ids_dates_and_outlets(stores):
    out = analyze_company_risk_news("리스크", "뉴로엣지")
    customer = _risk(out, "customer_concentration")
    assert customer["source"] == {"id": "NEWS-2026-10-02-01", "date": "2026-10-02", "name": "테크데일리 (가상)"}  # no link: no url
    assert out["coverage"]["stored_matches"] == 6 and out["freshness"]["fetched_now"] == 0


# ── Golden outputs: the scenario's results stay the same across refactors ──

GOLDEN = json.loads((Path(__file__).with_name("news_risk_golden.json")).read_text(encoding="utf-8"))


def test_risk_results_match_the_golden_file(stores):
    for case in GOLDEN["risk"]:
        r = analyze_company_risk_news(case["query"], case["company"], case["topics"], tool=k_tool if case["system"] == "K" else group_tool)
        got = [{k: x[k] for k in ("category", "level", "report_count", "outlet_count", "trend", "escalated_by_coverage")}
               | {"source_id": x["source"]["id"], "timeline": [e["id"] for e in x["timeline"]]} for x in r["risks"]]
        assert r["matched"] == case["matched"] and got == case["risks"], case["company"]
        assert [p["source"]["id"] for p in r["positive_signals"]] == case["positive"], case["company"]
        assert [[o["outlet"], o["negative"], o["positive"], o["neutral"], o["tone"]] for o in r["outlet_tone"]] == case["tone"], case["company"]


def test_briefing_topics_match_the_golden_file(stores):
    from app.systems.group.tools.run_news_briefing import run_news_briefing as group_briefing
    from app.systems.k_electronics.tools.run_news_briefing import run_news_briefing as k_briefing

    for name, ids in GOLDEN["briefing_topics"].items():
        tool, query = (k_briefing, "뉴로엣지") if name.startswith("K-") else (group_briefing, name[2:])
        r = tool.invoke({"query": query, "purpose": "risk"})
        assert sorted(sorted(a["id"] for a in t["articles"]) for t in r["topics"]) == ids, name
