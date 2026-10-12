from functools import partial

from app.library.workflows.company_risk_news_analysis import analyze_company_risk_news as _analyze_company_risk_news, requested_categories
from app.systems.k_electronics import DATA_DIR

# The workflow is system-neutral; these tests run it over K Electronics' article store.
analyze_company_risk_news = partial(_analyze_company_risk_news, path=DATA_DIR / "news.json")


def test_returns_three_neuroedge_risks_high_first():
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


def test_company_aliases_match_english_and_mixed_forms():
    for name in ("NeuroEdge", "neuroedge", "뉴로엣지(NeuroEdge)"):
        assert analyze_company_risk_news("risks", name)["matched"] == 3


def test_topics_narrow_to_requested_categories():
    out = analyze_company_risk_news("뉴로엣지 소송 현황", "뉴로엣지", ["법적 분쟁"])
    assert [r["category"] for r in out["risks"]] == ["legal_dispute"]
    out = analyze_company_risk_news("핵심인력 이탈 가능성", "뉴로엣지")
    assert [r["category"] for r in out["risks"]] == ["key_personnel"]


def test_no_topic_hint_returns_every_risk_of_the_company():
    out = analyze_company_risk_news("최근 악재", "뉴로엣지")
    assert out["matched"] == 3


def test_other_companies_are_filtered_out():
    out = analyze_company_risk_news("최대 고객사 리스크", "뉴로엣지", ["주요 고객"])
    assert [r["source"]["id"] for r in out["risks"]] == ["NEWS-2026-10-02-01"]
    out = analyze_company_risk_news("가동률", "오메가파운드리")
    assert [r["category"] for r in out["risks"]] == ["customer_concentration"]


def test_unknown_company_returns_empty():
    out = analyze_company_risk_news("리스크", "존재하지않는회사")
    assert out["matched"] == 0 and out["risks"] == []


def test_requested_categories_keywords():
    assert requested_categories("주요 고객 관련 리스크, 법적 분쟁, 핵심인력", []) == {
        "customer_concentration",
        "legal_dispute",
        "key_personnel",
    }
    assert requested_categories("최근 악재", []) == set()
