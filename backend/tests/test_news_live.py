"""Live news end to end (test database + fake web): request → feeds → pages → store → search and
reuse → events → briefing / risk view. Pages are written for these tests (fictional company 가온반도체)."""

import json
from datetime import datetime, timedelta

from app.library.tools.run_company_risk_news_analysis import make_run_company_risk_news_analysis
from app.library.tools.run_news_briefing import make_run_news_briefing
from app.library.workflows.news.feeds import KST, Feed
from app.library.workflows.news.fetch import Fetcher
from app.library.workflows.news.pipeline import Budget, gather
from app.library.workflows.news_briefing import run_live_briefing
from app.systems.k_electronics import DATA_DIR
from tests.test_news_pipeline import FakeWeb, article_html

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=KST)
BUDGET = Budget(seconds=30)


def rss(items):
    body = "".join(f"<item><title>{t}</title><link>{u}</link><description>{d}</description>"
                   f"<pubDate>{p}</pubDate></item>" for t, u, d, p in items)
    return f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>{body}</channel></rss>'.encode()


CUT = ("가온반도체는 최대 고객사인 한빛모바일로부터 내년 1분기 공급 물량을 30% 줄이겠다는 통보를 받았다고 14일 밝혔다. "
       "한빛모바일은 가온반도체 매출의 41%를 차지한다. 회사는 신규 고객 확보로 공백을 메우겠다는 입장이다. ")
PLANT = ("가온반도체는 13일 평택에서 신공장 착공식을 열었다. 총 2,000억 원이 투입되는 이 공장은 2028년 가동을 목표로 한다. "
         "회사는 신공장에서 차량용 전력반도체를 생산할 계획이다. ")
A_FEED = Feed("가상일보", "https://a.example/rss.xml")
B_FEED = Feed("모의경제", "https://b.example/rss.xml")


def web():
    pages = {
        A_FEED.url: (200, rss([
            ("가온반도체, 최대 고객사 물량 축소 통보받아", "https://a.example/1", "가온반도체가 최대 고객사로부터 물량 축소를 통보받았다.", "Sat, 14 Nov 2026 01:00:00 GMT"),
            ("가온반도체 신공장 착공", "https://a.example/2", "가온반도체가 신공장 착공식을 열었다.", "Fri, 13 Nov 2026 01:00:00 GMT"),
        ])),
        B_FEED.url: (200, rss([
            ("가온반도체 최대 고객사, 물량 축소 통보…매출 타격 우려", "https://b.example/9", "최대 고객사의 물량 축소로 매출 타격이 우려된다.", "Sat, 14 Nov 2026 03:00:00 GMT"),
        ])),
        "https://a.example/1": (200, article_html("가온반도체, 최대 고객사 물량 축소 통보받아", description="물량 축소 통보", paragraph=CUT)),
        "https://a.example/2": (200, article_html("가온반도체 신공장 착공", description="신공장 착공", paragraph=PLANT)),
        "https://b.example/9": (403, b""),  # blocked: stays at the feed's description
    }
    return FakeWeb(pages, robots={"a.example": (200, "User-agent: *\nAllow: /"), "b.example": (200, "User-agent: *\nAllow: /")})


FEEDS = [A_FEED, B_FEED]


def test_one_request_runs_the_whole_pipeline_and_the_next_reuses_it(news_db):
    first = gather(news_db, FEEDS, Fetcher(web(), host_gap=0), "가온반도체", now=NOW, budget=BUDGET)
    rows = {r["url"]: r for r in first["articles"]}
    assert set(rows) == {"https://a.example/1", "https://a.example/2", "https://b.example/9"}
    assert rows["https://a.example/1"]["content_level"] == "full" and rows["https://b.example/9"]["content_level"] == "snippet"
    assert first["freshness"]["fetched_now"] == 2 and first["freshness"]["discovered_now"] == 3
    # Two outlets' reports of the customer cut are one event; the plant is another.
    assert rows["https://a.example/1"]["event_id"] == rows["https://b.example/9"]["event_id"] != rows["https://a.example/2"]["event_id"]

    w = web()
    again = gather(news_db, FEEDS, Fetcher(w, host_gap=0), "가온반도체", now=NOW + timedelta(minutes=5), budget=BUDGET)
    assert w.asked == []  # feeds fresh, pages stored: nothing fetched
    assert again["freshness"]["reused_from_store"] == 3 and again["freshness"]["fetched_now"] == 0
    assert {r["id"]: r["event_id"] for r in again["articles"]} == {r["id"]: r["event_id"] for r in first["articles"]}  # stable ids
    with news_db.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM news_articles").fetchone()["n"] == 3
        mentions = conn.execute("SELECT surface, in_title FROM news_mentions").fetchall()
    assert {m["surface"] for m in mentions} == {"가온반도체"} and all(m["in_title"] for m in mentions)


def test_events_stay_put_and_new_reports_join_them(news_db):
    first = gather(news_db, [A_FEED], Fetcher(web(), host_gap=0), "가온반도체", now=NOW, budget=BUDGET)
    cut = next(r["event_id"] for r in first["articles"] if r["url"] == "https://a.example/1")
    later = gather(news_db, FEEDS, Fetcher(web(), host_gap=0), "가온반도체", now=NOW + timedelta(minutes=20), budget=BUDGET)
    joined = next(r for r in later["articles"] if r["url"] == "https://b.example/9")
    assert joined["event_id"] == cut  # the second outlet's report joined the existing event
    event = news_db.get_event(cut)
    assert event["article_ids"] and len(event["article_ids"]) == 2
    assert [e["id"] for e in news_db.events_for(surface="가온반도체")][:2] and news_db.provenance(joined["id"])["fetch_status"] == "blocked"


def test_the_live_briefing_answers_from_events_with_sources_and_freshness(news_db):
    out = run_live_briefing("가온반도체", store=news_db, feeds=FEEDS, fetcher=Fetcher(web(), host_gap=0), now=NOW, budget=BUDGET)
    top = out["topics"][0]
    assert top["outlet_count"] == 2 and top["event_id"] and "물량 축소" in top["articles"][0]["title"]
    assert all(a["url"].startswith("https://") and a["content_level"] for a in top["articles"])
    assert out["content_levels"] == {"full": 2, "snippet": 1}
    assert out["freshness"]["fetched_now"] == 2 and out["coverage"]["status"] == "ok"
    assert out["analysis"] == "rule" and top["sentiment"] == "negative"


def test_the_live_tools_keep_their_contracts(news_db, monkeypatch, tmp_path):
    from app.library.tools import run_news_briefing as briefing_module

    monkeypatch.setenv("NEWS_DATA_MODE", "live")
    monkeypatch.setattr(briefing_module, "_llm", lambda: None)
    feeds = tmp_path / "feeds.json"
    feeds.write_text(json.dumps({"feeds": [{"outlet": f.outlet, "url": f.url} for f in FEEDS]}, ensure_ascii=False), encoding="utf-8")
    briefing = make_run_news_briefing(DATA_DIR / "news.json", news_db, feeds=feeds, fetcher=Fetcher(web(), host_gap=0))
    out = briefing.invoke({"query": "가온반도체"})
    assert out["dataset"] == "live" and out["topics"] and all(a["outlet"] in ("가상일보", "모의경제") for t in out["topics"] for a in t["articles"])

    risk = make_run_company_risk_news_analysis(DATA_DIR / "news.json", news_db, feeds=feeds, fetcher=Fetcher(web(), host_gap=0))
    r = risk.invoke({"query": "가온반도체 리스크", "company": "가온반도체", "topics": ["주요 고객"]})
    assert r["dataset"] == "live" and r["matched"] == 1
    customer = r["risks"][0]
    assert customer["category"] == "customer_concentration" and customer["outlet_count"] == 2
    assert customer["source"]["id"].startswith("art_") and customer["source"]["url"].startswith("https://")  # risks[].source.id contract
    with news_db.connect() as conn:
        stored = {row["id"] for row in conn.execute("SELECT id FROM news_articles")}
    assert {t["id"] for t in customer["timeline"]} <= stored


def test_a_database_that_is_down_is_an_error_result_not_a_crash(monkeypatch):
    from app.library.tools import run_news_briefing as briefing_module
    from app.library.workflows.news.store import NewsStore

    monkeypatch.setenv("NEWS_DATA_MODE", "live")
    monkeypatch.setattr(briefing_module, "_llm", lambda: None)
    tool = make_run_news_briefing(DATA_DIR / "news.json", NewsStore("nowhere", dsn="postgresql://nobody@127.0.0.1:1/none"))
    out = tool.invoke({"query": "가온반도체"})
    assert out["dataset"] == "live" and "연결할 수 없습니다" in out["error"]


def test_a_wire_story_run_by_two_outlets_counts_once(news_db):
    wire = ("(서울=연합뉴스) 김기자 = 가온반도체가 최대 고객사로부터 물량 축소 통보를 받았다고 14일 밝혔다. "
            "한빛모바일은 가온반도체 매출의 41%를 차지한다. 회사는 신규 고객 확보로 공백을 메우겠다는 입장이다. ")
    c, d = Feed("지역일보", "https://c.example/rss.xml"), Feed("모의신문", "https://d.example/rss.xml")
    pages = {c.url: (200, rss([("가온반도체 물량 축소 통보", "https://c.example/5", "가온반도체 물량 축소", "Sat, 14 Nov 2026 02:00:00 GMT")])),
             d.url: (200, rss([("가온반도체, 최대 고객사 물량 축소 통보받아", "https://d.example/6", "가온반도체 물량 축소", "Sat, 14 Nov 2026 04:00:00 GMT")])),
             "https://c.example/5": (200, article_html("가온반도체 물량 축소 통보", paragraph=wire)),
             "https://d.example/6": (200, article_html("가온반도체, 최대 고객사 물량 축소 통보받아", paragraph=wire + "추가 문단. "))}
    w = FakeWeb(pages, robots={"c.example": (200, "User-agent: *\nAllow: /"), "d.example": (200, "User-agent: *\nAllow: /")})
    out = run_live_briefing("가온반도체", store=news_db, feeds=[c, d], fetcher=Fetcher(w, host_gap=0), now=NOW, budget=BUDGET)
    top = out["topics"][0]
    assert top["article_count"] == 2 and top["outlet_count"] == 2 and top["independent_outlet_count"] == 1  # one wire story, credited to 연합뉴스
    assert top["evidence"] == {"body": 2, "snippet": 0, "title": 0}
