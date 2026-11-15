import json
from functools import partial

from app.library.workflows.company_risk_news_analysis import analyze_company_risk_news as _analyze_company_risk_news, requested_categories
from app.systems.group import DATA_DIR as GROUP_DATA_DIR
from app.systems.k_electronics import DATA_DIR

# The workflow is system-neutral; these tests run it over K Electronics' article store.
analyze_company_risk_news = partial(_analyze_company_risk_news, path=DATA_DIR / "news.json")
GROUP_NEWS = GROUP_DATA_DIR / "news.json"


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
    assert requested_categories("규제 이슈랑 주가 반응", []) == {"regulatory", "market_reaction"}


# ── Follow-up reports, favourable news, collected store ────────────────────

def _risk(out, category):
    return next(r for r in out["risks"] if r["category"] == category)


def test_follow_up_reports_of_one_matter_become_one_risk():
    out = analyze_company_risk_news("뉴로엣지 소송 현황", "뉴로엣지", ["법적 분쟁"])
    legal = _risk(out, "legal_dispute")
    assert out["matched"] == 1 and legal["report_count"] == 2
    assert [t["id"] for t in legal["timeline"]] == ["NEWS-2026-09-24-01", "NEWS-2026-10-08-01"]  # oldest first
    assert legal["source"]["id"] == "NEWS-2026-10-08-01"  # lead: same level, newest
    assert legal["first_reported"] == "2026-09-24" and legal["last_reported"] == "2026-10-08"
    assert legal["trend"].startswith("지속")
    # Facts of every report, lead first.
    assert any("가처분" in f for f in legal["key_facts"]) and any("원고: 코어NPU" in f for f in legal["key_facts"])


def test_trend_and_level_of_a_matter_follow_its_reports():
    out = _analyze_company_risk_news("핵심인력 이탈", "K바이오", path=GROUP_NEWS)
    risk = out["risks"][0]
    assert risk["report_count"] == 2 and risk["level"] == "HIGH"  # the matter's highest level
    assert risk["trend"] == "완화"  # HIGH, then a MEDIUM follow-up
    assert risk["source"]["id"] == "NEWS-G-2026-10-05-01"  # lead: the most severe report


def test_every_risk_carries_check_points_for_its_kind():
    out = analyze_company_risk_news("최근 악재", "뉴로엣지")
    assert all(r["check_points"] for r in out["risks"])
    assert any("판매금지" in c for c in _risk(out, "legal_dispute")["check_points"])


def test_favourable_news_comes_back_apart_from_risks():
    out = analyze_company_risk_news("최근 악재", "뉴로엣지")
    assert out["matched"] == 3  # favourable news is not a risk
    assert [p["source"]["id"] for p in out["positive_signals"]] == ["NEWS-2026-10-06-01"]
    risk_ids = {t["id"] for r in out["risks"] for t in r["timeline"]}
    assert "NEWS-2026-10-06-01" not in risk_ids


def test_follow_ups_merge_for_key_personnel_too():
    people = _risk(analyze_company_risk_news("핵심인력", "뉴로엣지"), "key_personnel")
    assert people["report_count"] == 2
    assert people["source"]["id"] == "NEWS-2026-09-30-01"  # same level (HIGH): the newest leads
    assert any("스톡옵션" in f for f in people["key_facts"])


def test_collected_store_is_read_next_to_the_fixture(tmp_path):
    collected = tmp_path / "collected.json"
    collected.write_text(json.dumps({"articles": [{
        "id": "LIVE-abc", "date": "2026-10-20", "source": "example.com", "url": "https://example.com/a",
        "headline": "뉴로엣지 노조 설립 움직임", "summary": "임금 협상 결렬 시 쟁의 가능성.",
        "companies": ["뉴로엣지", "NeuroEdge"], "key_facts": ["노조 설립 움직임"],
        "risk": {"category": "labor_dispute", "label": "노사 분쟁", "level": "LOW"},
    }]}, ensure_ascii=False), encoding="utf-8")
    out = analyze_company_risk_news("최근 악재", "뉴로엣지", collected=collected)
    labor = _risk(out, "labor_dispute")
    assert out["matched"] == 4 and labor["source"]["url"] == "https://example.com/a"
    # A store that does not exist yet is simply empty.
    assert analyze_company_risk_news("최근 악재", "뉴로엣지", collected=tmp_path / "none.json")["matched"] == 3


def test_tool_reads_the_collected_store_beside_its_fixture(tmp_path):
    from app.library.tools.run_company_risk_news_analysis import make_run_company_risk_news_analysis
    from app.library.workflows.news_collection import COLLECTED_FILE

    fixture = tmp_path / "news.json"
    fixture.write_text((DATA_DIR / "news.json").read_text(encoding="utf-8"), encoding="utf-8")
    tool = make_run_company_risk_news_analysis(fixture)
    assert tool.invoke({"query": "리스크", "company": "뉴로엣지"})["matched"] == 3
    store = tmp_path / COLLECTED_FILE
    store.parent.mkdir()
    store.write_text(json.dumps({"articles": [{
        "id": "LIVE-x", "date": "2026-10-21", "source": "s", "headline": "뉴로엣지 노조 파업 예고", "summary": "",
        "companies": ["뉴로엣지"], "key_facts": ["파업 예고"], "risk": {"category": "labor_dispute", "label": "노사 분쟁", "level": "MEDIUM"},
    }]}, ensure_ascii=False), encoding="utf-8")
    assert tool.invoke({"query": "리스크", "company": "뉴로엣지"})["matched"] == 4  # picked up without a restart
