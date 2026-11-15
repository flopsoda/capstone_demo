"""News collection (app/library/workflows/news_collection.py): fetch → label → store, without network or LLM."""

import json
from datetime import date

from app.library.workflows.company_risk_news_analysis import analyze_company_risk_news
from app.library.workflows.news_collection import (
    COLLECTED_FILE,
    RawArticle,
    collect,
    fetch_google_news,
    fetch_naver_news,
    llm_label,
    merge,
    rule_label,
    watchlist_from,
)
from app.systems.k_electronics import DATA_DIR

TODAY = date(2026, 10, 31)

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss><channel>
<item><title>에이스칩, 美 업체에 특허침해 피소…판매금지 가처분까지 - 테크신문</title>
<link>https://news.example/1</link><pubDate>Thu, 29 Oct 2026 01:00:00 GMT</pubDate>
<description>&lt;b&gt;에이스칩&lt;/b&gt;이 특허침해 소송을 당했다. 원고는 판매금지 가처분도 신청했다.</description>
<source url="https://news.example">테크신문</source></item>
<item><title>에이스칩, 신제품 출시 - 경제일보</title>
<link>https://news.example/2</link><pubDate>Wed, 28 Oct 2026 01:00:00 GMT</pubDate>
<description>에이스칩이 신제품을 출시했다.</description><source url="https://e.example">경제일보</source></item>
<item><title>에이스칩 옛 기사 - 경제일보</title>
<link>https://news.example/old</link><pubDate>Mon, 01 Jun 2026 01:00:00 GMT</pubDate>
<description>오래된 기사.</description><source url="https://e.example">경제일보</source></item>
</channel></rss>"""


def fake_fetch(body: str, seen: list | None = None):
    def fetch(url, headers):
        if seen is not None:
            seen.append((url, headers))
        return body.encode()

    return fetch


def raw(title, summary="", url="https://x/1", day=date(2026, 10, 30)):
    return RawArticle(title, summary, url, "테스트일보", day, "에이스칩", ["AceChip"])


def test_google_news_rss_is_parsed_and_cleaned():
    seen = []
    items = fetch_google_news("에이스칩", ["AceChip"], 7, fetch=fake_fetch(RSS, seen))
    assert "news.google.com/rss/search" in seen[0][0]
    first = items[0]
    assert first.title == "에이스칩, 美 업체에 특허침해 피소…판매금지 가처분까지"  # " - 매체" removed
    assert first.publisher == "테크신문" and first.published == date(2026, 10, 29)
    assert "<b>" not in first.summary and first.id.startswith("LIVE-")


def test_neutral_collected_news_is_not_shown_as_good_news(tmp_path):
    store = tmp_path / "s.json"
    store.write_text(json.dumps({"articles": [
        {"id": "LIVE-n", "date": "2026-10-30", "source": "s", "headline": "에이스칩 주가 하락", "summary": "",
         "companies": ["에이스칩"], "risk": None, "sentiment": "neutral", "key_facts": []},
        {"id": "LIVE-p", "date": "2026-10-30", "source": "s", "headline": "에이스칩 대형 수주", "summary": "",
         "companies": ["에이스칩"], "risk": None, "sentiment": "positive", "key_facts": []},
    ]}, ensure_ascii=False), encoding="utf-8")
    out = analyze_company_risk_news("리스크", "에이스칩", path=DATA_DIR / "news.json", collected=store)
    assert [p["headline"] for p in out["positive_signals"]] == ["에이스칩 대형 수주"]


def test_naver_api_sends_keys_and_parses_items():
    body = json.dumps({"items": [{"title": "<b>에이스칩</b> 노조 파업 예고", "originallink": "https://www.daily.example/n/1",
                                  "description": "임금 협상 결렬.", "pubDate": "Fri, 30 Oct 2026 09:00:00 +0900"}]})
    seen = []
    items = fetch_naver_news("에이스칩", [], "id", "secret", fetch=fake_fetch(body, seen))
    assert seen[0][1] == {"X-Naver-Client-Id": "id", "X-Naver-Client-Secret": "secret"}
    assert items[0].title == "에이스칩 노조 파업 예고" and items[0].publisher == "daily.example"


def test_rule_label_category_and_level():
    high = rule_label(raw("에이스칩 특허침해 피소…판매금지 가처분"))
    assert high["risk"] == {"category": "legal_dispute", "label": "법적 분쟁", "level": "HIGH"}
    low = rule_label(raw("에이스칩 노조 설립 검토", "쟁의 가능성이 거론된다."))
    assert low["risk"]["category"] == "labor_dispute" and low["risk"]["level"] == "LOW"
    good = rule_label(raw("에이스칩, 신제품 출시"))
    assert good["risk"] is None and good["sentiment"] == "positive"
    assert rule_label(raw("에이스칩, 실리콘밸리 사무소 이전"))["sentiment"] == "neutral"  # neither: not good news
    record = rule_label(raw("에이스칩 CTO 사임", "CTO가 사임했다. 후임은 미정이다."))
    assert record["companies"] == ["에이스칩", "AceChip"] and record["key_facts"] == ["CTO가 사임했다.", "후임은 미정이다."]
    assert record["labeled_by"] == "rule"


def test_llm_labels_are_validated_and_bad_rows_fall_back():
    a, b, c = raw("기사 A", url="https://x/a"), raw("기사 B", url="https://x/b"), raw("기사 C", url="https://x/c")

    def llm(system, user):
        assert "JSON" in system
        return "결과:\n" + json.dumps([
            {"id": a.id, "category": "key_personnel", "level": "HIGH", "key_facts": ["CTO 사임"]},
            {"id": b.id, "category": "made_up", "level": "HIGH"},  # unknown category → rules
            {"id": c.id, "category": None, "positive": True, "key_facts": ["신제품"]},  # favourable
            {"id": "LIVE-not-asked", "category": "legal_dispute", "level": "LOW"},  # not in the batch → ignored
        ])

    out = llm_label([a, b, c], llm)
    assert set(out) == {a.id, c.id}
    assert out[a.id]["risk"]["level"] == "HIGH" and out[a.id]["key_facts"] == ["CTO 사임"] and out[a.id]["labeled_by"] == "llm"
    assert out[c.id]["risk"] is None and out[c.id]["sentiment"] == "positive"


def test_a_failing_llm_leaves_everything_to_the_rules():
    def broken(system, user):
        raise TimeoutError

    assert llm_label([raw("기사")], broken) == {}


def test_merge_skips_duplicates_and_drops_old_articles():
    old = {"id": "LIVE-old", "date": "2026-07-01", "source": "A", "headline": "아주 오래된 기사", "url": "https://x/old"}
    kept = {"id": "LIVE-1", "date": "2026-10-29", "source": "A", "headline": "에이스칩 특허침해 피소", "url": "https://x/1"}
    new = [
        {"id": "LIVE-1", "date": "2026-10-29", "source": "A", "headline": "에이스칩 특허침해 피소", "url": "https://x/1"},  # same id
        {"id": "LIVE-2", "date": "2026-10-29", "source": "A", "headline": "에이스칩 특허침해 피소!", "url": "https://x/2"},  # same outlet, same story
        {"id": "LIVE-3", "date": "2026-10-30", "source": "B", "headline": "에이스칩 특허침해 피소", "url": "https://y/3"},  # another outlet: coverage
        {"id": "LIVE-4", "date": "2026-10-30", "source": "A", "headline": "에이스칩 노조 파업 예고", "url": "https://x/4"},
    ]
    articles, added = merge([old, kept], new, today=TODAY, keep_days=90)
    assert added == 2 and sorted(a["id"] for a in articles) == ["LIVE-1", "LIVE-3", "LIVE-4"]


def test_a_passing_mention_is_neither_a_risk_nor_good_news():
    passing = rule_label(raw("SK하이닉스 노조 파업 예고…반도체 업계 긴장", "에이스칩 등 업계 전반에 영향."))
    assert passing["risk"] is None and passing["sentiment"] == "neutral"


def test_google_query_includes_aliases():
    seen = []
    fetch_google_news("에이스칩", ["AceChip"], 7, fetch=fake_fetch(RSS, seen))
    assert "%22AceChip%22" in seen[0][0] and "OR" in seen[0][0]


def _collected(id, day, source, headline, category="legal_dispute", level="MEDIUM"):
    return {"id": id, "date": day, "source": source, "headline": headline, "summary": "", "url": f"https://{id}",
            "companies": ["에이스칩", "AceChip"], "key_facts": [headline], "labeled_by": "rule", "sentiment": "negative",
            "risk": {"category": category, "label": "법적 분쟁", "level": level}}


def test_real_news_groups_by_story_and_counts_outlets(tmp_path):
    store = tmp_path / "s.json"
    store.write_text(json.dumps({"articles": [
        _collected("a", "2026-10-29", "테크신문", "에이스칩, 특허침해 피소…판매금지 가처분까지", level="HIGH"),
        _collected("b", "2026-10-30", "경제일보", "에이스칩 특허침해 피소, 판매금지 가처분 신청돼"),
        _collected("c", "2026-10-30", "경제일보", "에이스칩 주주단체, 경영진 상대 손해배상 소송"),
    ]}, ensure_ascii=False), encoding="utf-8")
    out = analyze_company_risk_news("소송", "에이스칩", path=DATA_DIR / "news.json", collected=store)
    assert out["matched"] == 2  # the patent suit and the shareholder suit stay apart
    patent = out["risks"][0]
    assert patent["report_count"] == 2 and patent["outlet_count"] == 2 and patent["level"] == "HIGH"
    assert patent["trend"].startswith("지속")  # a milder headline elsewhere is not "완화"


def test_collect_end_to_end_feeds_the_analysis(tmp_path):
    store = tmp_path / COLLECTED_FILE
    lines = []
    out = collect([("에이스칩", ["AceChip"])], store, days=7, fetch=fake_fetch(RSS), today=TODAY, log=lines.append)
    assert out["fetched"] == 2 and out["added"] == 2  # the June article is outside 7 days
    assert any(line.startswith("[store]") for line in lines)
    again = collect([("에이스칩", ["AceChip"])], store, days=7, fetch=fake_fetch(RSS), today=TODAY)
    assert again["added"] == 0 and again["total"] == 2  # running twice adds nothing

    result = analyze_company_risk_news("최근 리스크", "AceChip", path=DATA_DIR / "news.json", collected=store)
    assert [(r["category"], r["level"]) for r in result["risks"]] == [("legal_dispute", "HIGH")]
    assert result["risks"][0]["source"]["url"] == "https://news.example/1"
    assert [p["headline"] for p in result["positive_signals"]] == ["에이스칩, 신제품 출시"]


def test_one_company_failing_does_not_stop_the_rest(tmp_path):
    def fetch(url, headers):
        if "%EB%B2%A0%ED%83%80" in url:  # "베타"
            raise OSError("down")
        return RSS.encode()

    out = collect([("베타", []), ("에이스칩", [])], tmp_path / "s.json", fetch=fetch, today=TODAY)
    assert out["failed"] == ["베타"] and out["added"] == 2


def test_watchlist_comes_from_the_systems_company_list():
    assert ("뉴로엣지", ["NeuroEdge"]) in watchlist_from(DATA_DIR / "companies.json")


def test_rules_cover_regulation_market_reaction_and_operations():
    assert rule_label(raw("에이스칩 주주환원, 금산법에 막히나"))["risk"]["category"] == "regulatory"
    assert rule_label(raw("에이스칩 역대급 실적에도 주가 흘러내렸다"))["risk"]["category"] == "market_reaction"
    assert rule_label(raw("에이스칩, 수율 문제로 생산 차질"))["risk"]["category"] == "operations"
    assert rule_label(raw("에이스칩 3분기 영업익 첫 100조 돌파"))["sentiment"] == "positive"


def test_opinion_pieces_are_at_most_low():
    column = rule_label(raw("[데스크 칼럼] 에이스칩 성과급 갈등, 회사를 쪼개야 할까"))
    assert column["risk"]["category"] == "labor_dispute" and column["risk"]["level"] == "LOW"


def test_market_reaction_is_one_theme_and_wide_coverage_raises_the_level(tmp_path):
    store = tmp_path / "s.json"
    store.write_text(json.dumps({"articles": [
        _collected("m1", "2026-10-29", "한국경제", "에이스칩 역대급 실적에도 주가 흘러내렸다", "market_reaction"),
        _collected("m2", "2026-10-29", "비즈니스포스트", "에이스칩 소극적 주주환원에 외신 비판", "market_reaction"),
        _collected("m3", "2026-10-30", "연합뉴스", "에이스칩 실망 매물에 코스피 급락", "market_reaction"),
        _collected("m4", "2026-11-20", "연합뉴스", "에이스칩 주가 또 약세", "market_reaction"),  # weeks later: a new matter
    ]}, ensure_ascii=False), encoding="utf-8")
    out = analyze_company_risk_news("주가", "에이스칩", path=DATA_DIR / "news.json", collected=store)
    theme = next(r for r in out["risks"] if r["report_count"] == 3)
    assert theme["outlet_count"] == 3 and theme["level"] == "HIGH" and theme["escalated_by_coverage"] is True
    assert out["matched"] == 2


def test_good_news_is_one_entry_per_story_most_covered_first(tmp_path):
    def good(id, source, headline):
        return {**_collected(id, "2026-10-29", source, headline), "risk": None, "sentiment": "positive"}

    store = tmp_path / "s.json"
    store.write_text(json.dumps({"articles": [
        good("p1", "A", "에이스칩, 주당 4601원 배당한다"),
        good("p2", "B", "[속보] 에이스칩, 주당 4601원 배당"),
        good("p3", "C", "에이스칩 브랜드 가치 7년 연속 5위"),
    ]}, ensure_ascii=False), encoding="utf-8")
    out = analyze_company_risk_news("리스크", "에이스칩", path=DATA_DIR / "news.json", collected=store)
    assert [(p["outlet_count"], "배당" in p["headline"]) for p in out["positive_signals"]] == [(2, True), (1, False)]


def test_outlet_tone_counts_each_outlets_lean(tmp_path):
    def art(id, source, sentiment, risky=False):
        a = _collected(id, "2026-10-29", source, f"에이스칩 기사 {id}")
        return a if risky else {**a, "risk": None, "sentiment": sentiment}

    store = tmp_path / "s.json"
    store.write_text(json.dumps({"articles": [
        art("1", "가신문", "negative", risky=True), art("2", "가신문", "negative", risky=True), art("3", "가신문", "neutral"),
        art("4", "나일보", "positive"), art("5", "나일보", "positive"),
        art("6", "다경제", "positive"), art("7", "다경제", "negative", risky=True),
    ]}, ensure_ascii=False), encoding="utf-8")
    out = analyze_company_risk_news("리스크", "에이스칩", path=DATA_DIR / "news.json", collected=store)
    tone = {t["outlet"]: (t["tone"], t["negative"], t["positive"]) for t in out["outlet_tone"]}
    assert tone == {"가신문": ("비우호", 2, 0), "나일보": ("우호", 0, 2), "다경제": ("중립", 1, 1)}
    assert out["outlet_tone"][0]["outlet"] == "가신문"  # most negative first


def test_one_article_is_not_enough_to_judge_an_outlet(tmp_path):
    store = tmp_path / "s.json"
    store.write_text(json.dumps({"articles": [_collected("1", "2026-10-29", "라신문", "에이스칩 소송")]}, ensure_ascii=False), encoding="utf-8")
    out = analyze_company_risk_news("리스크", "에이스칩", path=DATA_DIR / "news.json", collected=store)
    assert out["outlet_tone"][0]["tone"] == "판단 보류"
