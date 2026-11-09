"""News delivery (app/library/workflows/news_delivery.py): personalize per subscriber, then send
by Telegram / e-mail. No network: fake sources, a fake Telegram API and a fake SMTP server."""

import json
import shutil
from datetime import date

from app.library.workflows.news_briefing import FixtureSource, GoogleNewsSource
from app.library.workflows.news_delivery import (
    EmailSender,
    PrintSender,
    SubscriberStore,
    TelegramSender,
    deliver,
    format_briefing,
    manage_interests,
    ollama_http_llm,
    personal_briefing,
    run_delivery,
    senders_from_env,
)
from app.systems.k_electronics import DATA_DIR

TODAY = date(2026, 11, 10)
RSS = """<rss><channel>
<item><title>HBM 시장 공급 부족 전망 - 테크신문</title><link>https://n.example/1</link>
<pubDate>Mon, 09 Nov 2026 01:00:00 GMT</pubDate><description>공급 부족.</description><source url="x">테크신문</source></item>
<item><title>HBM 시장 채용 공고 쏟아져 - 잡뉴스</title><link>https://n.example/2</link>
<pubDate>Mon, 09 Nov 2026 02:00:00 GMT</pubDate><description>채용 공고.</description><source url="x">잡뉴스</source></item>
</channel></rss>"""


def data(tmp_path):
    for name in ("news.json", "news_subscribers.json"):
        shutil.copy(DATA_DIR / name, tmp_path / name)
    return tmp_path


def sources(tmp_path):
    return [GoogleNewsSource(fetch=lambda url, headers: RSS.encode()), FixtureSource(tmp_path / "news.json")]


def test_a_briefing_follows_the_subscribers_interests_and_exclusions(tmp_path):
    d = data(tmp_path)
    ceo = SubscriberStore(d).find("ceo@example.com")
    b = personal_briefing(ceo, sources=sources(d), today=TODAY)
    assert [s["query"] for s in b["sections"]] == ["뉴로엣지", "HBM 시장", "AI 반도체 수출 규제"]
    titles = [t["articles"][0]["title"] for t in b["topics"]]
    assert not any("채용 공고" in t for t in titles)  # the CEO excluded it
    assert {t["interest"] for t in b["topics"]} >= {"뉴로엣지", "HBM 시장"}
    assert [t["importance"] for t in b["topics"]] == sorted((t["importance"] for t in b["topics"]), reverse=True)
    assert b["period"] == "daily" and b["alerts"]
    urls = [a.get("url") for t in b["topics"] for a in t["articles"] if a.get("url")]
    assert len(urls) == len(set(urls))  # every interest's search found the same live story: listed once


def test_no_user_means_the_first_demo_profile(tmp_path):
    assert SubscriberStore(data(tmp_path)).find(None)["id"] == "k_ceo"
    assert SubscriberStore(data(tmp_path)).find("nobody@example.com") is None


def test_interest_changes_go_to_the_runtime_copy_not_the_fixture(tmp_path):
    d = data(tmp_path)
    store = SubscriberStore(d)
    out = manage_interests(store, None, "add", "삼성전자", "risk")
    assert {"query": "삼성전자", "purpose": "risk"} in out["subscriber"]["interests"]
    manage_interests(store, None, "remove", "HBM 시장")
    manage_interests(store, None, "set_briefing", briefing="weekly")
    me = store.find(None)
    assert [i["query"] for i in me["interests"]] == ["뉴로엣지", "AI 반도체 수출 규제", "삼성전자"] and me["briefing"] == "weekly"
    assert "삼성전자" not in (d / "news_subscribers.json").read_text(encoding="utf-8")
    assert "error" in manage_interests(store, None, "remove", "없는 주제")
    assert "error" in manage_interests(store, None, "set_briefing", briefing="hourly")


def test_the_briefing_text(tmp_path):
    d = data(tmp_path)
    subject, text = format_briefing(personal_briefing(SubscriberStore(d).find(None), sources=sources(d), today=TODAY))
    assert subject.startswith("[일간 뉴스 브리핑] 2026-11-10")
    assert "주요 이슈" in text and "⚠ 부정 기사 알림" in text and "https://n.example/1" in text


def test_telegram_posts_to_the_bot_api_in_chunks():
    calls = []

    def post(url, body):
        calls.append((url, json.loads(body)))
        return b'{"ok": true}'

    TelegramSender("TOKEN", "42", post=post).send("s", "가" * 5000)
    assert [c[0] for c in calls] == ["https://api.telegram.org/botTOKEN/sendMessage"] * 2
    assert calls[0][1]["chat_id"] == "42" and len(calls[0][1]["text"]) == 4000


def test_email_logs_in_and_sends():
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            sent.append(("login", user))

        def send_message(self, msg):
            sent.append((msg["To"], msg["Subject"]))

    EmailSender("smtp.example", 465, "me@example.com", "pw", "boss@example.com", smtp=FakeSMTP).send("제목", "본문")
    assert sent == [("login", "me@example.com"), ("boss@example.com", "제목")]


def test_one_channel_failing_does_not_stop_the_other():
    class Down:
        def send(self, subject, text):
            raise OSError("no route")

    ok = PrintSender()
    out = deliver("s", "t", ["telegram", "email", "sms"], {"telegram": Down(), "email": ok})
    assert out["sent"] == ["email"] and "telegram" in out["failed"] and out["not_configured"] == ["sms"]


def test_channels_come_only_from_the_environment():
    senders, missing = senders_from_env({"NEWS_TELEGRAM_BOT_TOKEN": "t", "NEWS_TELEGRAM_CHAT_ID": "1"})
    assert list(senders) == ["telegram"] and len(missing) == 1
    assert senders_from_env({})[0] == {}


def test_alerts_are_sent_once_per_story(tmp_path):
    d = data(tmp_path)
    out = PrintSender()
    first = run_delivery(d, "alerts", sources=sources(d), senders={"telegram": out, "email": out}, today=TODAY, log=lambda m: None)
    again = run_delivery(d, "alerts", sources=sources(d), senders={"telegram": out, "email": out}, today=TODAY, log=lambda m: None)
    assert first[0]["alerts"] >= 1 and again[0]["alerts"] == 0
    assert [r["subscriber"] for r in first] == ["k_ceo"]  # the strategy team has alerts off
    assert all(s.startswith("[부정 기사 알림]") for s, _ in out.sent)


def test_scheduled_briefing_goes_to_every_subscriber(tmp_path):
    d = data(tmp_path)
    out = PrintSender()
    report = run_delivery(d, "briefing", sources=sources(d), senders={"telegram": out, "email": out}, today=TODAY, log=lambda m: None)
    assert [r["subscriber"] for r in report] == ["k_ceo", "k_strategy"]
    assert out.sent[1][0].startswith("[주간 뉴스 브리핑]")  # the strategy team reads weekly


def test_plain_python_llm_calls_ollama_chat(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "k")
    monkeypatch.setenv("OLLAMA_MODEL", "gemma4:31b-cloud")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    seen = {}

    def post(url, body):
        seen["url"], seen["body"] = url, json.loads(body)
        return b'{"message": {"content": "{}"}}'

    assert ollama_http_llm(post)("sys", "user") == "{}"
    assert seen["url"] == "https://ollama.com/api/chat" and seen["body"]["model"] == "gemma4:31b-cloud"


def test_an_alert_says_which_of_our_records_it_touches(tmp_path):
    d = data(tmp_path)
    shutil.copy(DATA_DIR / "companies.json", d / "companies.json")
    out = PrintSender()
    run_delivery(d, "alerts", sources=sources(d), senders={"telegram": out}, today=TODAY, log=lambda m: None)
    text = "\n".join(t for _, t in out.sent)
    assert "↔ 검토 자료: 최대 고객 알파모바일 매출 의존도 46%" in text
