"""News end to end (test database + fake web): request → feeds → pages → store → search and
reuse → events → briefing / risk view. Pages are written for these tests (fictional company 가온반도체)."""

import json
from datetime import datetime, timedelta

import pytest

from app.library.tools.run_company_risk_news_analysis import make_run_company_risk_news_analysis
from app.library.tools.run_news_briefing import make_run_news_briefing
from app.library.workflows.news.feeds import KST, Feed
from app.library.workflows.news.fetch import Fetcher
from app.library.workflows.news.pipeline import Budget, gather
from app.library.workflows.news_briefing import run_news_briefing
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


def test_the_briefing_answers_from_events_with_sources_and_freshness(news_db):
    out = run_news_briefing("가온반도체", store=news_db, feeds=FEEDS, fetcher=Fetcher(web(), host_gap=0), now=NOW, budget=BUDGET)
    top = out["topics"][0]
    assert top["outlet_count"] == 2 and top["event_id"] and "물량 축소" in top["articles"][0]["title"]
    assert all(a["url"].startswith("https://") and a["content_level"] for a in top["articles"])
    assert out["content_levels"] == {"full": 2, "snippet": 1}
    assert out["freshness"]["fetched_now"] == 2 and out["coverage"]["status"] == "ok"
    assert out["analysis"] == "rule" and top["sentiment"] == "negative"


def test_the_tools_keep_their_contracts(news_db, monkeypatch, tmp_path):
    from app.library.tools import run_news_briefing as briefing_module

    monkeypatch.setattr(briefing_module, "_llm", lambda: None)
    feeds = tmp_path / "feeds.json"
    feeds.write_text(json.dumps({"feeds": [{"outlet": f.outlet, "url": f.url} for f in FEEDS]}, ensure_ascii=False), encoding="utf-8")
    briefing = make_run_news_briefing(news_db, feeds=feeds, fetcher=Fetcher(web(), host_gap=0))
    out = briefing.invoke({"query": "가온반도체"})
    assert out["topics"] and all(a["outlet"] in ("가상일보", "모의경제") for t in out["topics"] for a in t["articles"])

    risk = make_run_company_risk_news_analysis(news_db, feeds=feeds, fetcher=Fetcher(web(), host_gap=0))
    r = risk.invoke({"query": "가온반도체 리스크", "company": "가온반도체", "topics": ["주요 고객"]})
    assert r["matched"] == 1
    customer = r["risks"][0]
    assert customer["category"] == "customer_concentration" and customer["outlet_count"] == 2
    assert customer["source"]["id"].startswith("art_") and customer["source"]["url"].startswith("https://")  # risks[].source.id contract
    with news_db.connect() as conn:
        stored = {row["id"] for row in conn.execute("SELECT id FROM news_articles")}
    assert {t["id"] for t in customer["timeline"]} <= stored


def test_a_database_that_is_down_is_an_error_result_not_a_crash(monkeypatch):
    from app.library.tools import run_news_briefing as briefing_module
    from app.library.workflows.news.store import NewsStore

    monkeypatch.setattr(briefing_module, "_llm", lambda: None)
    tool = make_run_news_briefing(NewsStore("nowhere", dsn="postgresql://nobody@127.0.0.1:1/none"))
    out = tool.invoke({"query": "가온반도체"})
    assert "연결할 수 없습니다" in out["error"]


def test_a_wire_story_run_by_two_outlets_counts_once(news_db):
    wire = ("(서울=연합뉴스) 김기자 = 가온반도체가 최대 고객사로부터 물량 축소 통보를 받았다고 14일 밝혔다. "
            "한빛모바일은 가온반도체 매출의 41%를 차지한다. 회사는 신규 고객 확보로 공백을 메우겠다는 입장이다. ")
    c, d = Feed("지역일보", "https://c.example/rss.xml"), Feed("모의신문", "https://d.example/rss.xml")
    pages = {c.url: (200, rss([("가온반도체 물량 축소 통보", "https://c.example/5", "가온반도체 물량 축소", "Sat, 14 Nov 2026 02:00:00 GMT")])),
             d.url: (200, rss([("가온반도체, 최대 고객사 물량 축소 통보받아", "https://d.example/6", "가온반도체 물량 축소", "Sat, 14 Nov 2026 04:00:00 GMT")])),
             "https://c.example/5": (200, article_html("가온반도체 물량 축소 통보", paragraph=wire)),
             "https://d.example/6": (200, article_html("가온반도체, 최대 고객사 물량 축소 통보받아", paragraph=wire + "추가 문단. "))}
    w = FakeWeb(pages, robots={"c.example": (200, "User-agent: *\nAllow: /"), "d.example": (200, "User-agent: *\nAllow: /")})
    out = run_news_briefing("가온반도체", store=news_db, feeds=[c, d], fetcher=Fetcher(w, host_gap=0), now=NOW, budget=BUDGET)
    top = out["topics"][0]
    assert top["article_count"] == 2 and top["outlet_count"] == 2 and top["independent_outlet_count"] == 1  # one wire story, credited to 연합뉴스
    assert top["evidence"] == {"body": 2, "snippet": 0, "title": 0}


def test_article_analyses_are_stored_and_reused(news_db):
    from tests.test_news_briefing import fake_llm

    calls: list[str] = []
    first = run_news_briefing("가온반도체", store=news_db, feeds=FEEDS, fetcher=Fetcher(web(), host_gap=0), now=NOW,
                              budget=BUDGET, llm=fake_llm({}, calls=calls))
    assert calls.count("article") == 1 and first["article_analysis"] == ["llm"]
    calls.clear()
    again = run_news_briefing("가온반도체", store=news_db, feeds=FEEDS, fetcher=Fetcher(web(), host_gap=0),
                              now=NOW + timedelta(minutes=5), budget=BUDGET, llm=fake_llm({}, calls=calls))
    assert calls == ["event"]  # articles already analyzed for this target and version
    with news_db.connect() as conn:
        rows = conn.execute("SELECT target, analyzer_version, stance, basis FROM news_article_analysis").fetchall()
        events = conn.execute("SELECT event_id::text AS id, impact FROM news_event_analysis").fetchall()
    assert {r["target"] for r in rows} == {"가온반도체"} and {r["analyzer_version"] for r in rows} == {"article-v1"}
    assert {r["basis"] for r in rows} == {"body", "snippet"} and all(r["stance"] == "unknown" for r in rows if r["basis"] == "snippet")
    assert {e["id"] for e in events} == {t["event_id"] for t in again["topics"]}


def test_the_risk_view_reads_each_articles_own_risk_and_stance(news_db, monkeypatch, tmp_path):
    from app.library.tools import run_news_briefing as briefing_module

    def llm(system, user):  # the customer cut is a risk to 가온반도체; the plant is good news, no risk
        arts = json.loads(user.split("\n\n기사:\n", 1)[1])
        return json.dumps([{"id": a["id"], "stance": "critical" if "물량" in a["title"] else "favorable", "article_type": "report",
                            "risk": {"category": "customer_concentration", "level": "HIGH"} if "물량" in a["title"] else None,
                            "key_facts": [a["title"]]} for a in arts], ensure_ascii=False)

    monkeypatch.setattr(briefing_module, "_llm", lambda: llm)
    feeds = tmp_path / "feeds.json"
    feeds.write_text(json.dumps({"feeds": [{"outlet": f.outlet, "url": f.url} for f in FEEDS]}, ensure_ascii=False), encoding="utf-8")
    risk = make_run_company_risk_news_analysis(news_db, feeds=feeds, fetcher=Fetcher(web(), host_gap=0))
    r = risk.invoke({"query": "가온반도체 리스크", "company": "가온반도체"})
    assert [(x["category"], x["level"]) for x in r["risks"]] == [("customer_concentration", "HIGH")]
    assert [p["headline"] for p in r["positive_signals"]] == ["가온반도체 신공장 착공"]
    tone = {o["outlet"]: (o["negative"], o["positive"]) for o in r["outlet_tone"]}
    assert tone["가상일보"] == (1, 1)  # one critical, one favorable article: the outlet's own articles, one by one


def test_search_ignores_spacing_marks(news_db):
    feed = Feed("가상일보", "https://e.example/rss.xml")
    items = [("원·달러 환율 1,400원 돌파", "https://e.example/1", "원·달러 환율이 올랐다.", "Sat, 14 Nov 2026 01:00:00 GMT"),
             ("2차 전지 소재 수출 증가", "https://e.example/2", "2차전지 소재 수출이 늘었다.", "Sat, 14 Nov 2026 02:00:00 GMT")]
    w = FakeWeb({feed.url: (200, rss(items))}, robots={"e.example": (200, "User-agent: *\nDisallow: /1\nDisallow: /2")})
    fx = gather(news_db, [feed], Fetcher(w, host_gap=0), "원달러 환율", now=NOW, budget=BUDGET)
    assert [r["url"] for r in fx["articles"]] == ["https://e.example/1"]  # "원·달러" is "원달러"
    battery = gather(news_db, [feed], Fetcher(w, host_gap=0), "2차전지 업황", now=NOW, budget=BUDGET)
    assert battery["articles"] == [] and battery["coverage"]["search"]["mode"] == "keyword"  # no article says "업황"; no words dropped


# ── Semantic search (a fake embedder: character-bigram vectors, deterministic) ──

class BigramEmbedder:
    model = "test-bigram"
    cutoff = (0.05, 1.0)

    def __init__(self):
        self.documents = 0

    @staticmethod
    def _vector(text):
        from app.library.workflows.news.text import bigrams

        v = [0.0] * 128
        for b in bigrams(text):
            v[int(__import__("hashlib").md5(b.encode()).hexdigest(), 16) % 128] += 1.0
        n = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / n for x in v]

    def embed_query(self, text):
        return self._vector(text)

    def embed_documents(self, texts):
        self.documents += len(texts)
        return [self._vector(t) for t in texts]


class DownEmbedder(BigramEmbedder):
    def embed_query(self, text):
        from app.library.workflows.news.embed import EmbedUnavailable

        raise EmbedUnavailable("test-bigram: ConnectError")


def battery_web():
    feed = Feed("가상일보", "https://e.example/rss.xml")
    items = [("2차 전지 소재 수출 증가", "https://e.example/2", "2차전지 소재 수출이 늘었다.", "Sat, 14 Nov 2026 02:00:00 GMT"),
             ("프로야구 개막전 매진", "https://e.example/3", "개막전 표가 모두 팔렸다.", "Sat, 14 Nov 2026 03:00:00 GMT")]
    return [feed], FakeWeb({feed.url: (200, rss(items))}, robots={"e.example": (200, "User-agent: *\nDisallow: /2\nDisallow: /3")})


def test_semantic_search_finds_what_the_keywords_miss_and_reuses_the_vectors(news_db):
    feeds, w = battery_web()
    emb = BigramEmbedder()
    out = gather(news_db, feeds, Fetcher(w, host_gap=0), "2차전지 업황", embedder=emb, now=NOW, budget=BUDGET)
    search = out["coverage"]["search"]
    assert [r["url"] for r in out["articles"]][:1] == ["https://e.example/2"]  # no article says "업황"; the meaning finds it
    assert search["mode"] == "hybrid" and search["keyword_matches"] == 0 and search["semantic_only"] >= 1
    assert search["embedded_now"] == 2 and out["articles"][0]["similarity"] > 0.3
    with news_db.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM news_article_embeddings WHERE model = 'test-bigram'").fetchone()["n"] == 2
    emb.documents = 0
    again = gather(news_db, feeds, Fetcher(w, host_gap=0), "2차전지 업황", embedder=emb, now=NOW + timedelta(minutes=5), budget=BUDGET)
    assert emb.documents == 0 and again["coverage"]["search"]["embedded_now"] == 0  # stored vectors reused
    assert [r["id"] for r in again["articles"]] == [r["id"] for r in out["articles"]]


def test_keywords_and_meaning_together_rank_an_article_found_both_ways_first(news_db):
    feeds, w = battery_web()
    out = gather(news_db, feeds, Fetcher(w, host_gap=0), "2차전지", embedder=BigramEmbedder(), now=NOW, budget=BUDGET)
    assert out["articles"][0]["url"] == "https://e.example/2" and out["coverage"]["search"]["keyword_matches"] == 1


def test_without_the_embedding_model_the_search_falls_back_to_keywords(news_db):
    feeds, w = battery_web()
    out = gather(news_db, feeds, Fetcher(w, host_gap=0), "2차전지", embedder=DownEmbedder(), now=NOW, budget=BUDGET)
    search = out["coverage"]["search"]
    assert [r["url"] for r in out["articles"]] == ["https://e.example/2"]
    assert search["mode"] == "keyword" and "ConnectError" in search["error"] and "키워드로만" in out["coverage"]["note"]


def test_semantic_hits_never_become_another_companys_risk(news_db, monkeypatch):
    from app.library.tools import run_company_risk_news_analysis as risk_module
    from app.library.tools import run_news_briefing as briefing_module
    from app.systems.k_electronics.tools.run_company_risk_news_analysis import run_company_risk_news_analysis as tool

    emb = BigramEmbedder()
    monkeypatch.setattr(briefing_module, "_llm", lambda: None)
    monkeypatch.setattr(risk_module, "default_embedder", lambda: emb)
    r = tool.invoke({"query": "뉴로엣지 인수 판단에 영향을 줄 외부 요인", "company": "뉴로엣지"})
    assert r["coverage"]["search"]["mode"] == "hybrid" and r["coverage"]["search"]["semantic_only"] == 0
    reports = {t["id"] for x in r["risks"] for t in x["timeline"]}
    assert "NEWS-2026-09-18-01" not in reports  # 오메가파운드리's customer cut is close in meaning, not 뉴로엣지's risk
    assert r["matched"] == 3 and emb.documents == 9  # the registered articles were embedded on this first request


def _local_embedding_model():
    from app.library.workflows.news.embed import Embedder, EmbedUnavailable

    emb = Embedder()
    try:
        emb.embed_query("확인")
    except EmbedUnavailable:
        return None
    return emb


@pytest.mark.network
def test_the_real_embedding_model_finds_a_conceptual_request(news_db):
    """Local Ollama with embeddinggemma (skipped when it is not running)."""
    emb = _local_embedding_model()
    if emb is None:
        pytest.skip("local Ollama with embeddinggemma is not available")
    from app.systems.k_electronics import NEWS_STORE

    out = gather(NEWS_STORE, [], Fetcher(FakeWeb({}), host_gap=0), "뉴로엣지 기술 경쟁력과 관련된 위험 요소", embedder=emb, days=120)
    search = out["coverage"]["search"]
    assert search["keyword_matches"] == 0 and search["mode"] == "hybrid"  # no article has every word
    found = [r["id"] for r in out["articles"]]
    assert sum(i in found for i in ("NEWS-2026-09-24-01", "NEWS-2026-09-27-01", "NEWS-2026-09-30-01", "NEWS-2026-10-08-01")) >= 3


# ── Articles a system registers from its data file (store seed) ─────────────

def _counts(store):
    with store.connect() as conn:
        return {t: conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
                for t in ("news_outlets", "news_articles", "news_events", "news_event_articles", "news_article_analysis", "news_mentions")}


def test_the_seed_registers_once_and_keeps_its_ids(news_db):
    from app.library.workflows.news.seed import event_id
    from app.systems.k_electronics import NEWS_STORE

    first = _counts(NEWS_STORE)
    assert first["news_articles"] == 9 and first["news_events"] == 6  # the 11/17 report joins the 10/02 event
    NEWS_STORE.reset()  # a restart: the file is applied again
    assert _counts(NEWS_STORE) == first
    article = NEWS_STORE.get_article("NEWS-2026-10-08-01")
    assert article["url"] is None and article["outlet"] == "반도체경제 (가상)"
    assert article["published_at"].astimezone(KST).date().isoformat() == "2026-10-08"  # the article's own date
    assert article["event_id"] == event_id("kelec_news", "corenpu-patent-suit")
    assert NEWS_STORE.get_event(article["event_id"])["article_ids"] == ["NEWS-2026-09-24-01", "NEWS-2026-10-08-01"]
    prov = NEWS_STORE.provenance("NEWS-2026-10-08-01")
    assert prov["fetch_status"] == "imported" and prov["discovered_at"] and prov["fetched_at"] is None
    assert {e["id"] for e in NEWS_STORE.events_for(surface="뉴로엣지")} >= {article["event_id"]}  # the KG's way in


def test_registered_and_collected_articles_are_one_store(news_db, monkeypatch, tmp_path):
    from app.library.tools import run_news_briefing as briefing_module
    from app.systems.k_electronics import NEWS_STORE

    asked: list[str] = []

    def llm(system, user):  # only articles without a stored analysis reach the model
        arts = json.loads(user.split("\n\n기사:\n", 1)[1])
        asked.extend(a["id"] for a in arts)
        return json.dumps([{"id": a["id"], "stance": "critical", "article_type": "report",
                            "risk": {"category": "regulatory", "level": "MEDIUM"}, "key_facts": [a["title"]]} for a in arts],
                          ensure_ascii=False)

    feed = Feed("가상일보", "https://f.example/rss.xml")
    now = datetime.now(KST)
    pages = {feed.url: (200, rss([("뉴로엣지 NPU 수출, 전략물자 심사 대상 지정", "https://f.example/1",
                                     "뉴로엣지의 NPU 수출이 전략물자 심사 대상이 됐다.", now.strftime("%a, %d %b %Y %H:%M:%S +0900"))]))}
    w = FakeWeb(pages, robots={"f.example": (200, "User-agent: *\nDisallow: /1")})
    feeds = tmp_path / "feeds.json"
    feeds.write_text(json.dumps({"feeds": [{"outlet": feed.outlet, "url": feed.url}]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(briefing_module, "_llm", lambda: llm)
    tool = make_run_company_risk_news_analysis(NEWS_STORE, feeds=feeds, fetcher=Fetcher(w, host_gap=0))
    r = tool.invoke({"query": "뉴로엣지 리스크", "company": "뉴로엣지"})
    assert asked == ["art_" + __import__("hashlib").sha256(b"https://f.example/1").hexdigest()[:20]]  # the new one only
    categories = {x["category"]: x["source"]["id"] for x in r["risks"]}
    assert categories["customer_concentration"] == "NEWS-2026-11-17-01" and categories["regulatory"].startswith("art_")
    assert r["coverage"]["stored_matches"] == 8 and r["freshness"]["discovered_now"] == 1
