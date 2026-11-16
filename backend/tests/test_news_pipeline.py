"""News pipeline stages (app/library/workflows/news/): feeds → fetch → extract → store.

No network: a fake web (FakeWeb) answers every request. The pages are written for these tests
(fictional companies and events), never copied articles. Store tests need the test database
(conftest.news_db) and skip without it.
"""

from datetime import datetime, timedelta

import pytest

from app.library.workflows.news.extract import body_hash, clean_body, extract
from app.library.workflows.news.feeds import KST, Feed, normalize_url, outlet_domain, parse_date, parse_feed
from app.library.workflows.news.fetch import Fetcher, FetchError
from app.library.workflows.news.pipeline import Budget, ingest
from app.library.workflows.news.store import article_id, migrations

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=KST)

PARAGRAPH = ("가온반도체는 15일 차세대 전력반도체 생산라인을 증설한다고 밝혔다. 회사는 내년 상반기까지 1,200억 원을 투자해 "
             "청주 공장의 월 생산능력을 두 배로 늘릴 계획이다. 업계는 전기차 수요 둔화에도 산업용 수요가 견조하다고 본다. ")


def article_html(title: str, paragraphs: int = 4, *, notice: str = "ⓒ 가상일보, 무단 전재 및 재배포 금지",
                 description: str = "가온반도체가 전력반도체 생산라인 증설에 나선다.", extra: str = "",
                 paragraph: str = PARAGRAPH) -> bytes:
    body = "".join(f"<p>{paragraph}{i}</p>" for i in range(paragraphs))
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{title}</title>
<meta property="og:title" content="{title}"><meta property="og:description" content="{description}">
<link rel="canonical" href="https://news.example/articles/1">
<meta property="article:published_time" content="2026-11-15T09:00:00+09:00"></head>
<body><nav><a href="/">홈</a><a href="/economy">경제</a></nav>
<article><h1>{title}</h1>{body}<p>홍길동 기자 gildong@news.example</p><p>{notice}</p>{extra}</article>
<aside><h3>많이 본 뉴스</h3><ul><li>다른 기사</li></ul></aside></body></html>""".encode()


RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>가상일보</title>
<item><title>가온반도체, 전력반도체 라인 증설</title><link>https://news.example/articles/1?utm_source=rss</link>
<description>가온반도체가 1,200억 원을 들여 생산라인을 늘린다.</description><pubDate>Sun, 15 Nov 2026 00:00:00 GMT</pubDate></item>
<item><title>가온반도체 노조, 임금 협상 결렬</title><link>https://news.example/articles/2</link>
<description>가온반도체 노사의 임금 협상이 결렬됐다.</description><pubDate>Sun, 15 Nov 2026 01:00:00 GMT</pubDate></item>
<item><title>날씨: 전국 대체로 맑음</title><link>https://news.example/articles/3</link>
<description>주말 날씨.</description><pubDate>Sun, 15 Nov 2026 02:00:00 GMT</pubDate></item>
</channel></rss>"""


class FakeWeb:
    def __init__(self, pages: dict[str, tuple[int, bytes]], robots: dict[str, tuple[int, str]] | None = None):
        self.pages, self.robots, self.asked = pages, robots or {}, []

    def __call__(self, url, headers):
        self.asked.append(url)
        assert "CapstoneNewsAgent" in headers["User-Agent"]
        if url.endswith("/robots.txt"):
            host = url.split("/")[2]
            status, text = self.robots.get(host, (404, ""))
            return status, url, "text/plain", text.encode()
        if url not in self.pages:
            return 404, url, "text/html", b""
        status, body = self.pages[url]
        return status, url, "text/html", body


def fetcher(web):
    return Fetcher(web, host_gap=0)


# ── feeds ────────────────────────────────────────────────────────────────────

def test_rss_items_are_normalized():
    items = parse_feed(RSS.encode(), Feed("가상일보", "https://news.example/rss.xml"))
    assert [i.title for i in items][:2] == ["가온반도체, 전력반도체 라인 증설", "가온반도체 노조, 임금 협상 결렬"]
    first = items[0]
    assert first.url == "https://news.example/articles/1"  # tracking parameter dropped
    assert first.published == datetime(2026, 11, 15, 9, 0, tzinfo=KST) and first.outlet == "가상일보"
    assert first.domain == "news.example"


def test_atom_sitemap_euckr_and_broken_feeds_parse():
    atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>아톰 기사</title>
<link rel="alternate" href="https://a.example/1"/><published>2026-11-14T10:00:00+09:00</published><summary>요약</summary></entry></feed>"""
    assert [i.url for i in parse_feed(atom.encode(), Feed("A", "https://a.example/atom"))] == ["https://a.example/1"]
    sitemap = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
xmlns:news="http://www.google.com/schemas/sitemap-news/0.9"><url><loc>https://s.example/n/7</loc>
<news:news><news:publication_date>2026-11-15</news:publication_date><news:title>사이트맵 기사</news:title></news:news></url></urlset>"""
    item = parse_feed(sitemap.encode(), Feed("S", "https://s.example/sitemap.xml", "sitemap"))[0]
    assert item.title == "사이트맵 기사" and item.published.date().isoformat() == "2026-11-15"
    euckr = '<?xml version="1.0" encoding="EUC-KR"?><rss><channel><item><title>한글 제목</title><link>https://e.example/1</link></item></channel></rss>'
    assert parse_feed(euckr.encode("cp949"), Feed("E", "https://e.example/rss"))[0].title == "한글 제목"
    broken = "<rss><channel><item><title>A & B 합병</title><link>https://b.example/1</link></item></channel></rss>"
    assert parse_feed(broken.encode(), Feed("B", "https://b.example/rss"))[0].title == "A & B 합병"


def test_url_identity_and_outlet_domain():
    assert normalize_url("HTTPS://News.Example/a?id=3&utm_medium=x#top") == "https://news.example/a?id=3"
    assert article_id("https://news.example/a?id=3") == article_id(normalize_url("https://news.example/a?id=3&fbclid=z"))
    assert outlet_domain("https://www.yna.co.kr/view/1") == "yna.co.kr" and outlet_domain("https://view.asiae.co.kr/a") == "asiae.co.kr"
    assert parse_date("2026-11-15 18:30") == datetime(2026, 11, 15, 18, 30, tzinfo=KST)


# ── extract ──────────────────────────────────────────────────────────────────

def test_a_full_article_comes_out_clean():
    x = extract(article_html("가온반도체, 전력반도체 라인 증설"), "https://news.example/articles/1")
    assert x.content_level == "full" and "1,200억 원" in x.body
    assert "무단 전재" not in x.body and "gildong@" not in x.body and "많이 본 뉴스" not in x.body
    assert x.title == "가온반도체, 전력반도체 라인 증설" and x.published.date().isoformat() == "2026-11-15"
    assert x.canonical_url == "https://news.example/articles/1" and not x.usage_notice


def test_a_page_without_article_text_falls_back_to_its_description():
    js_only = """<html><head><title>T</title><meta property="og:description" content="자바스크립트로만 그리는 페이지의 요약 문장입니다. 본문은 없습니다."></head>
<body><div id="root"></div><script>render()</script></body></html>""".encode()
    x = extract(js_only, "https://news.example/js")
    assert x.content_level == "snippet" and x.body == "" and "요약 문장" in x.description
    bare = extract(b"<html><body><div id='app'></div></body></html>", "https://news.example/empty")
    assert bare.content_level == "title"
    short = extract(article_html("속보", paragraphs=2), "https://news.example/s")
    assert short.content_level == "partial"


def test_an_explicit_ai_notice_is_recorded():
    x = extract(article_html("기사", notice="<저작권자(c) 가상통신, 무단 전재-재배포, AI 학습 및 활용 금지>"), "https://news.example/n")
    assert x.usage_notice and "AI 학습" not in x.body


def test_clean_body_and_hash():
    text = "본문 첫 문단입니다.\n본문 첫 문단입니다.\n이 기사를 추천합니다\n김기자 kim@x.example\n짧음"
    assert clean_body(text) == "본문 첫 문단입니다."
    assert body_hash("가 나\n다") == body_hash("가나다") and body_hash("") is None


# ── fetch ────────────────────────────────────────────────────────────────────

def test_robots_txt_is_respected_and_access_limits_are_not_worked_around():
    page = article_html("기사")
    web = FakeWeb({"https://ok.example/a": (200, page), "https://ok.example/private/a": (200, page),
                   "https://paywall.example/a": (403, b""), "https://cf.example/a": (200, b"<title>Just a moment...</title>"),
                   "https://down.example/a": (200, page)},
                  robots={"ok.example": (200, "User-agent: *\nDisallow: /private/\n\nUser-agent: GPTBot\nDisallow: /"),
                          "down.example": (503, "")})
    f = fetcher(web)
    assert f.get("https://ok.example/a").body == page  # the AI-training-crawler group does not apply to us
    for url, status in (("https://ok.example/private/a", "robots_disallowed"), ("https://paywall.example/a", "blocked"),
                        ("https://cf.example/a", "blocked"), ("https://down.example/a", "robots_disallowed")):
        with pytest.raises(FetchError) as e:
            f.get(url)
        assert e.value.status == status, url
    assert "https://ok.example/private/a" not in web.asked  # never requested
    assert "https://down.example/a" not in web.asked  # robots.txt unreachable (5xx): treated as disallow-all
    assert web.asked.count("https://ok.example/robots.txt") == 1  # cached per host


def test_network_errors_become_statuses():
    def broken(url, headers):
        raise TimeoutError("slow")

    with pytest.raises(FetchError) as e:
        Fetcher(broken, host_gap=0).get("https://slow.example/a")
    assert e.value.status == "robots_disallowed"  # robots.txt itself timed out: nothing is fetched


# ── store + ingest (test database) ───────────────────────────────────────────

FEED = Feed("가상일보", "https://news.example/rss.xml")


def site(**overrides):
    pages = {"https://news.example/rss.xml": (200, RSS.encode()),
             "https://news.example/articles/1": (200, article_html("가온반도체, 전력반도체 라인 증설")),
             "https://news.example/articles/2": (200, article_html("가온반도체 노조, 임금 협상 결렬", description="임금 협상 결렬"))}
    pages.update(overrides)
    return FakeWeb(pages, robots={"news.example": (200, "User-agent: *\nAllow: /\n")})


def articles(store):
    with store.connect() as conn:
        return {r["url"]: r for r in conn.execute("SELECT * FROM news_articles")}


def test_schema_is_versioned_and_migrates_once(news_db):
    applied = news_db.migrate()
    assert applied == [v for v, _ in migrations()] and applied[0] == 1
    news_db.reset()
    assert news_db.migrate() == []  # already applied
    with news_db.connect() as conn:
        assert [r["version"] for r in conn.execute("SELECT version FROM schema_version")] == applied


def test_ingest_discovers_every_item_and_fetches_the_pages_the_request_needs(news_db):
    web = site()
    stats = ingest(news_db, [FEED], fetcher(web), terms=["가온반도체"], now=NOW, budget=Budget(seconds=30))
    assert stats["discovered"] == 3 and stats["pages_tried"] == 2 and stats["page_results"] == {"full": 2}
    rows = articles(news_db)
    assert rows["https://news.example/articles/1"]["content_level"] == "full"
    assert rows["https://news.example/articles/1"]["fetched_at"] is not None
    assert rows["https://news.example/articles/1"]["feed_url"] == FEED.url
    weather = rows["https://news.example/articles/3"]  # not asked about: feed data only, fetched later
    assert weather["content_level"] == "title" and weather["fetch_status"] == "pending"
    assert "https://news.example/articles/3" not in web.asked


def test_the_same_article_is_stored_once_and_feeds_are_not_repolled_within_the_ttl(news_db):
    ingest(news_db, [FEED], fetcher(site()), terms=["가온반도체"], now=NOW, budget=Budget(seconds=30))
    web = site()
    again = ingest(news_db, [FEED], fetcher(web), terms=["가온반도체"], now=NOW + timedelta(minutes=5), budget=Budget(seconds=30))
    assert again["feeds_fresh"] == 1 and again["discovered"] == 0 and again["pages_tried"] == 0
    assert web.asked == []  # nothing fetched: the database already has it
    later = ingest(news_db, [FEED], fetcher(site()), terms=["가온반도체"], now=NOW + timedelta(minutes=20), budget=Budget(seconds=30))
    assert later["feeds_polled"] == 1 and later["discovered"] == 0  # polled again, same URLs: no duplicates
    assert len(articles(news_db)) == 3


def test_failures_are_recorded_and_do_not_stop_the_run(news_db):
    down = Feed("고장일보", "https://down.example/rss.xml")
    web = site(**{"https://news.example/articles/2": (403, b"")})
    stats = ingest(news_db, [down, FEED], fetcher(web), terms=["가온반도체"], now=NOW, budget=Budget(seconds=30))
    assert stats["feeds_failed"] and stats["page_results"] == {"full": 1, "blocked": 1}
    rows = articles(news_db)
    blocked = rows["https://news.example/articles/2"]
    assert blocked["fetch_status"] == "blocked" and blocked["content_level"] == "snippet"  # the feed's description stays
    with news_db.connect() as conn:
        feeds = {r["url"]: r["last_status"] for r in conn.execute("SELECT url, last_status FROM news_feeds")}
        runs = conn.execute("SELECT trigger, stats FROM news_ingest_runs").fetchall()
    assert feeds["https://down.example/rss.xml"] != "ok" and feeds[FEED.url] == "ok"
    assert runs[0]["trigger"] == "request" and runs[0]["stats"]["discovered"] == 3


def test_the_page_budget_leaves_the_rest_pending(news_db):
    stats = ingest(news_db, [FEED], fetcher(site()), terms=None, now=NOW, budget=Budget(seconds=30, bodies=1))
    assert stats["pages_tried"] == 1
    assert sum(r["fetch_status"] == "pending" for r in articles(news_db).values()) == 2


def test_search_reuses_stored_articles_by_any_text(news_db):
    ingest(news_db, [FEED], fetcher(site()), terms=["가온반도체"], now=NOW, budget=Budget(seconds=30))
    hits = news_db.search(["가온반도체", "청주"], since=NOW - timedelta(days=7))  # "청주" is only in the bodies
    assert {h["url"] for h in hits} == {"https://news.example/articles/1", "https://news.example/articles/2"}
    assert all(h["outlet"] == "가상일보" for h in hits)
    assert news_db.search(["가온반도체"], since=NOW + timedelta(days=1)) == []  # outside the window


def test_store_unavailable_without_a_database():
    from app.library.workflows.meeting_store import StoreUnavailable
    from app.library.workflows.news.store import NewsStore

    with pytest.raises(StoreUnavailable):
        NewsStore("nowhere").migrate()


def test_korean_page_furniture_is_cleaned():
    text = "\n".join([
        "가온반도체, 라인 증설",  # the headline again at the top
        "회사 측 \"수요 견조\"",  # machine summary lines …
        "전체 내용을 이해하기 위해서는 기사 본문과 함께 읽어야 합니다",  # … and its disclaimer
        "등록 2026.11.15 09:00:00수정 2026.11.15 10:00:00",
        "가온반도체는 15일 생산라인 증설 계획을 발표했다. 투자 규모는 1,200억 원이다. 홍길동 기자 gildong@",
        "◎공감언론 가상통신 [email protected]",
        "AI 추천 뉴스",
    ])
    assert clean_body(text, "가온반도체, 라인 증설") == "가온반도체는 15일 생산라인 증설 계획을 발표했다. 투자 규모는 1,200억 원이다."


def test_copies_and_wire_stories_are_found():
    from app.library.workflows.news.cluster import Item, independent_outlet, syndicated

    lead = "(서울=연합뉴스) 김기자 = 가온반도체가 최대 고객사로부터 물량 축소 통보를 받았다고 14일 밝혔다. 회사는 대응책을 마련 중이다."
    t0 = datetime(2026, 11, 14, 9, tzinfo=KST)
    items = [Item("a", "물량 축소", "연합뉴스", t0, lead=lead, body_hash="h1", has_body=True),
             Item("b", "물량 축소 통보", "지역일보", t0 + timedelta(hours=1), lead=lead, body_hash="h2", has_body=True),
             Item("c", "다른 기사", "모의경제", t0 + timedelta(hours=2), lead="전혀 다른 문장으로 시작하는 기사 본문입니다. 다른 내용이다.", has_body=True),
             Item("d", "같은 본문", "다른일보", t0 + timedelta(hours=3), lead="x", body_hash="h1")]
    assert syndicated(items) == {"b": "a", "d": "a"}
    assert independent_outlet("지역일보", lead) == "연합뉴스" and independent_outlet("연합뉴스", lead) == "연합뉴스"
    assert independent_outlet("모의경제", items[2].lead) == "모의경제"
