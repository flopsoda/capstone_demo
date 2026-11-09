"""News delivery — the last two stages of the News Agent's pipeline (the original design):

    4. personalize : each subscriber's interests (topics + purpose), excluded words and briefing
                     period → one briefing per person, built from run_news_briefing per interest
    5. deliver     : daily / weekly briefing and negative-article alerts by Telegram and e-mail,
                     on a schedule (this module's CLI, run by Task Scheduler / cron) or on request
                     (the agent's run_personal_news_briefing Tool)

Subscribers (data/news_subscribers.json) are fictional demo profiles. Changes made through the
agent are written to data/collected/news_subscribers.json (runtime data, not committed) and
read in preference to the fixture. Where messages go — the bot token, chat id, SMTP account and
recipient — comes only from the environment (NEWS_TELEGRAM_*, NEWS_SMTP_*, NEWS_EMAIL_TO), never
from these files: the demo sends every subscriber's briefing to the one configured recipient.

No LangChain dependency, standard library only (urllib, smtplib).
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import sys
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from .news_briefing import LLM, PURPOSES, Source, context_from_companies, run_news_briefing, state_dir, topic_line
from .news_collection import KST

SUBSCRIBERS_FILE = "news_subscribers.json"
OVERRIDE_FILE = "news_subscribers.json"  # in state_dir(): runtime copy once changed
SENT_ALERTS_FILE = "news_alerts_sent.json"
HISTORY_FILE = "news_history.json"
PERIODS = {"daily": 1, "weekly": 7}
BRIEFING_TOPICS = 6  # topics in one person's briefing, across all interests
TELEGRAM_LIMIT = 4000  # Telegram allows 4096 characters per message
ALERT_MIN_IMPORTANCE = 50
# An alert interrupts an executive: a negative story qualifies only if it touches one of our own
# records (internal_links) or many outlets carry it. Everything else waits for the briefing.
ALERT_MIN_OUTLETS = 3


# ── 4. Personalize ──────────────────────────────────────────────────────────

class SubscriberStore:
    """The system's subscriber profiles: the committed fictional fixture, or the runtime copy once
    something was changed."""

    def __init__(self, data_dir: Path):
        self.fixture = data_dir / SUBSCRIBERS_FILE
        self.override = state_dir(data_dir) / OVERRIDE_FILE

    def load(self) -> list[dict[str, Any]]:
        path = self.override if self.override.exists() else self.fixture
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))["subscribers"]

    def save(self, subscribers: list[dict[str, Any]]) -> None:
        self.override.parent.mkdir(parents=True, exist_ok=True)
        self.override.write_text(json.dumps({"subscribers": subscribers}, ensure_ascii=False, indent=2), encoding="utf-8")

    def find(self, who: str | None) -> dict[str, Any] | None:
        """By id or e-mail; no one named → the first profile (local UIs and tests run without a user)."""
        subs = self.load()
        if not who:
            return subs[0] if subs else None
        who = who.lower()
        return next((s for s in subs if who in (s["id"].lower(), str(s.get("email", "")).lower())), None)


def manage_interests(store: SubscriberStore, who: str | None, action: str, query: str = "", purpose: str = "general",
                     briefing: str = "") -> dict[str, Any]:
    """action: list | add | remove | set_briefing (daily/weekly) | exclude | include."""
    subs = store.load()
    me = store.find(who)
    if not me:
        return {"error": "뉴스 구독 정보가 없는 사용자입니다."}
    me = next(s for s in subs if s["id"] == me["id"])
    query = (query or "").strip()
    interests: list[dict[str, Any]] = me.setdefault("interests", [])
    excluded: list[str] = me.setdefault("exclude", [])
    if action == "add":
        if not query:
            return {"error": "추가할 관심 주제가 없습니다."}
        interests[:] = [i for i in interests if i["query"] != query]
        interests.append({"query": query, "purpose": purpose if purpose in PURPOSES else "general"})
    elif action == "remove":
        if not any(i["query"] == query for i in interests):
            return {"error": f"관심 주제에 '{query}'가 없습니다.", "interests": interests}
        interests[:] = [i for i in interests if i["query"] != query]
    elif action == "exclude" and query:
        if query not in excluded:
            excluded.append(query)
    elif action == "include" and query:
        excluded[:] = [w for w in excluded if w != query]
    elif action == "set_briefing":
        if briefing not in PERIODS:
            return {"error": "브리핑 주기는 daily 또는 weekly입니다."}
        me["briefing"] = briefing
    elif action != "list":
        return {"error": f"모르는 action: {action}"}
    if action != "list":
        store.save(subs)
    return {"subscriber": _profile(me), "changed": action != "list"}


def _profile(s: dict[str, Any]) -> dict[str, Any]:
    return {k: s.get(k) for k in ("id", "name", "role", "interests", "exclude", "briefing", "alerts", "channels")}


def personal_briefing(sub: dict[str, Any], *, sources: list[Source], llm: LLM | None = None, days: int | None = None,
                      today: date | None = None, log: Callable[[str], None] | None = None,
                      context_for: Callable[[str], list[dict[str, Any]]] | None = None,
                      history: Path | None = None) -> dict[str, Any]:
    """One person's briefing: run_news_briefing per interest with that interest's purpose, topics
    with an excluded word dropped, the most important topics across interests first."""
    log = log or (lambda msg: None)
    days = days or PERIODS.get(sub.get("briefing", "daily"), 1)
    excluded = [w.lower() for w in sub.get("exclude", [])]
    sections, topics, alerts = [], [], []
    for interest in sub.get("interests", []):
        out = run_news_briefing(interest["query"], purpose=interest.get("purpose", "general"), days=days,
                                sources=sources, llm=llm, today=today,
                                context=context_for(interest["query"]) if context_for else None, history=history)
        kept = [t for t in out["topics"]
                if not any(w in (a["title"] + a["summary"]).lower() for a in t["articles"] for w in excluded)]
        log(f"  {interest['query']}: 기사 {out['collected']}건 → 주제 {len(kept)}개 ({out['analysis']})")
        for t in kept:
            t["interest"] = interest["query"]
        sections.append({"query": interest["query"], "purpose": out["purpose"], "trend": out["trend"],
                         "so_what": out["so_what"], "analysis": out["analysis"], "topic_count": len(kept),
                         "failed_sources": out["failed_sources"]})
        topics.extend(kept)
        alerts.extend(t for t in kept if t["id"] in out["negative_alerts"] and alert_worthy(t))
    topics.sort(key=lambda t: t["importance"], reverse=True)
    # Interests overlap ("HBM 시장" and "AI 반도체"): a story appears once, under its best-scored interest.
    seen: set[str] = set()
    unique = []
    for t in topics:
        keys = {a.get("url") or a["title"] for a in t["articles"]}
        if not keys & seen:
            unique.append(t)
        seen |= keys
    alerts = [t for t in alerts if t in unique]
    topics = unique
    return {
        "subscriber": _profile(sub),
        "period": "weekly" if days >= 7 else "daily",
        "days": days,
        "date": (today or datetime.now(KST).date()).isoformat(),
        "sections": sections,
        "topics": topics[:BRIEFING_TOPICS],
        "alerts": alerts,
    }


def alert_worthy(t: dict[str, Any]) -> bool:
    return bool(t.get("internal_links")) or t["outlet_count"] >= ALERT_MIN_OUTLETS


def format_briefing(b: dict[str, Any]) -> tuple[str, str]:
    """(subject, plain text) — the same text goes to Telegram and e-mail."""
    who = b["subscriber"]
    kind = "주간" if b["period"] == "weekly" else "일간"
    subject = f"[{kind} 뉴스 브리핑] {b['date']} · {who['name']}"
    lines = [subject, f"관심 주제: {', '.join(s['query'] for s in b['sections']) or '없음'}", ""]
    for s in b["sections"]:
        if s["trend"]:
            lines.append(f"■ {s['query']} — {s['trend']}")
    if any(s["trend"] for s in b["sections"]):
        lines.append("")
    lines.append("주요 이슈")
    if not b["topics"]:
        lines.append("- 기간 안에 새 기사가 없습니다.")
    for t in b["topics"]:
        lines.append(f"- ({t['interest']}) {topic_line(t)}")
        lines += _internal_lines(t)
        url = next((a.get("url") for a in t["articles"] if a.get("url")), None)
        if url:
            lines.append(f"  {url}")
    so_whats = [f"- {s['query']}: {s['so_what']}" for s in b["sections"] if s["so_what"]]
    if so_whats:
        lines += ["", "시사점(So-What)", *so_whats]
    if b["alerts"]:
        lines += ["", "⚠ 부정 기사 알림", *(f"- ({t['interest']}) {t['articles'][0]['title']}" for t in b["alerts"])]
    return subject, "\n".join(lines)


def _internal_lines(t: dict[str, Any]) -> list[str]:
    """What of ours the story bears on, and how it moved since the last look."""
    lines = []
    if t.get("internal_note"):
        lines.append(f"  → {t['internal_note']}")
    for link in t.get("internal_links") or []:
        lines.append(f"  ↔ {link['kind']}: {link['text']} ({link['source']})")
    tr = t.get("tracking") or {}
    if tr.get("status") == "new":
        lines.append("  ● 지난 확인 이후 새로 나온 이야기")
    elif tr.get("status") == "developing":
        lines.append(f"  ▲ 후속 보도 {tr['new_articles']}건 · 보도 매체 {tr['outlets_before']}→{tr['outlets_now']}곳")
    return lines


def format_alert(t: dict[str, Any]) -> tuple[str, str]:
    lead = t["articles"][0]
    risk = f" · {t['risk']['label']} {t['risk']['level']}" if t.get("risk") else ""
    subject = f"[부정 기사 알림] {t['interest']}{risk}"
    body = [subject, lead["title"], f"{lead['outlet']} · {lead['date']} · 매체 {t['outlet_count']}곳 보도", t["summary"]]
    body += _internal_lines(t)
    if lead.get("url"):
        body.append(lead["url"])
    return subject, "\n".join(body)


# ── 5. Deliver ──────────────────────────────────────────────────────────────

Post = Callable[[str, bytes], bytes]


def http_post(url: str, body: bytes) -> bytes:
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


@dataclass
class TelegramSender:
    token: str
    chat_id: str
    post: Post = http_post
    name: str = "telegram"

    def send(self, subject: str, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        for start in range(0, len(text), TELEGRAM_LIMIT):
            body = {"chat_id": self.chat_id, "text": text[start : start + TELEGRAM_LIMIT], "disable_web_page_preview": True}
            answer = json.loads(self.post(url, json.dumps(body).encode()))
            if not answer.get("ok"):
                raise RuntimeError(answer.get("description", "Telegram 발송 실패"))


@dataclass
class EmailSender:
    host: str
    port: int
    user: str
    password: str
    to: str
    smtp: Callable[..., Any] = smtplib.SMTP_SSL
    name: str = "email"

    def send(self, subject: str, text: str) -> None:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, self.user, self.to
        msg.set_content(text)
        with self.smtp(self.host, self.port, timeout=20) as server:
            server.login(self.user, self.password)
            server.send_message(msg)


@dataclass
class PrintSender:
    """--dry-run and tests: what would be sent."""
    name: str = "print"
    sent: list[tuple[str, str]] = field(default_factory=list)

    def send(self, subject: str, text: str) -> None:
        self.sent.append((subject, text))
        print("\n" + "─" * 60 + "\n" + text + "\n" + "─" * 60)


def senders_from_env(env: dict[str, str] | None = None) -> tuple[dict[str, Any], list[str]]:
    """Channels that are configured, and a note for each one that is not."""
    env = os.environ if env is None else env
    get = lambda k: (env.get(k) or "").strip()  # noqa: E731
    senders: dict[str, Any] = {}
    missing = []
    if get("NEWS_TELEGRAM_BOT_TOKEN") and get("NEWS_TELEGRAM_CHAT_ID"):
        senders["telegram"] = TelegramSender(get("NEWS_TELEGRAM_BOT_TOKEN"), get("NEWS_TELEGRAM_CHAT_ID"))
    else:
        missing.append("telegram: NEWS_TELEGRAM_BOT_TOKEN, NEWS_TELEGRAM_CHAT_ID 필요")
    if get("NEWS_SMTP_USER") and get("NEWS_SMTP_PASSWORD") and get("NEWS_EMAIL_TO"):
        senders["email"] = EmailSender(get("NEWS_SMTP_HOST") or "smtp.gmail.com", int(get("NEWS_SMTP_PORT") or 465),
                                       get("NEWS_SMTP_USER"), get("NEWS_SMTP_PASSWORD"), get("NEWS_EMAIL_TO"))
    else:
        missing.append("email: NEWS_SMTP_USER, NEWS_SMTP_PASSWORD, NEWS_EMAIL_TO 필요")
    return senders, missing


def deliver(subject: str, text: str, channels: list[str], senders: dict[str, Any]) -> dict[str, Any]:
    """Sends to each of the subscriber's channels that is configured. One channel failing does not
    stop the others."""
    sent, failed, skipped, used = [], {}, [], set()
    for ch in channels:
        sender = senders.get(ch)
        if not sender:
            skipped.append(ch)
            continue
        if id(sender) in used:  # --dry-run prints every channel with one sender
            sent.append(ch)
            continue
        used.add(id(sender))
        try:
            sender.send(subject, text)
            sent.append(ch)
        except Exception as e:  # network, auth: report it, keep going
            failed[ch] = f"{type(e).__name__}: {e}"[:200]
    return {"sent": sent, "failed": failed, "not_configured": skipped}


def _alert_key(t: dict[str, Any]) -> str:
    lead = t["articles"][0]
    return lead.get("url") or f"{lead['outlet']}|{lead['title']}"


def new_alerts(b: dict[str, Any], sent_path: Path) -> list[dict[str, Any]]:
    """Alerts this subscriber was not sent before (the same story is alerted once)."""
    sent = json.loads(sent_path.read_text(encoding="utf-8")) if sent_path.exists() else {}
    mine = set(sent.get(b["subscriber"]["id"], []))
    return [t for t in b["alerts"] if _alert_key(t) not in mine]


def mark_alerted(b: dict[str, Any], alerts: list[dict[str, Any]], sent_path: Path) -> None:
    sent = json.loads(sent_path.read_text(encoding="utf-8")) if sent_path.exists() else {}
    keys = sent.setdefault(b["subscriber"]["id"], [])
    keys.extend(_alert_key(t) for t in alerts if _alert_key(t) not in keys)
    sent[b["subscriber"]["id"]] = keys[-500:]
    sent_path.parent.mkdir(parents=True, exist_ok=True)
    sent_path.write_text(json.dumps(sent, ensure_ascii=False, indent=2), encoding="utf-8")


def run_delivery(data_dir: Path, mode: str, *, sources: list[Source], senders: dict[str, Any], llm: LLM | None = None,
                 who: str | None = None, days: int | None = None, today: date | None = None,
                 log: Callable[[str], None] = print) -> list[dict[str, Any]]:
    """mode "briefing": each subscriber's daily/weekly briefing. mode "alerts": only negative
    articles not alerted before, to subscribers with alerts on."""
    store = SubscriberStore(data_dir)
    subs = [store.find(who)] if who else store.load()
    report = []
    for sub in filter(None, subs):
        if mode == "alerts" and not sub.get("alerts", True):
            continue
        log(f"[{sub['name']}] {mode}")
        b = personal_briefing(sub, sources=sources, llm=llm, days=days, today=today, log=log,
                              context_for=lambda q: context_from_companies(data_dir / "companies.json", q),
                              history=state_dir(data_dir) / HISTORY_FILE)
        channels = sub.get("channels", ["telegram", "email"])
        if mode == "alerts":
            fresh = new_alerts(b, state_dir(data_dir) / SENT_ALERTS_FILE)
            results = [deliver(*format_alert(t), channels, senders) for t in fresh]
            if fresh and any(r["sent"] for r in results):
                mark_alerted(b, fresh, state_dir(data_dir) / SENT_ALERTS_FILE)
            report.append({"subscriber": sub["id"], "alerts": len(fresh), "results": results})
            log(f"  새 부정 기사 {len(fresh)}건")
        else:
            result = deliver(*format_briefing(b), channels, senders)
            report.append({"subscriber": sub["id"], "topics": len(b["topics"]), **result})
            log(f"  발송: {result}")
    return report


# ── CLI (the scheduler runs this) ───────────────────────────────────────────

def load_env_file(path: Path) -> None:
    """backend/.env without python-dotenv (the CLI also runs on a plain Python)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#") and value.strip() and not os.environ.get(key.strip()):
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def cli_llm() -> LLM | None:
    """The backend's configured model; on a plain Python (no LangChain) Ollama's HTTP API directly."""
    try:
        from ...runtime.llm import LLMConfigError, get_chat_model
    except ImportError:
        return ollama_http_llm()
    try:
        model = get_chat_model()
    except LLMConfigError:
        return None
    return lambda system, user: str(model.invoke([("system", system), ("human", user)]).content)


def ollama_http_llm(post: Post | None = None) -> LLM | None:
    key = os.environ.get("OLLAMA_API_KEY", "").strip()
    if not key or (os.environ.get("LLM_PROVIDER", "ollama").strip() or "ollama") != "ollama":
        return None
    base = (os.environ.get("OLLAMA_BASE_URL") or "https://ollama.com").strip().rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "").strip() or "gemma4:31b-cloud"

    def default_post(url: str, body: bytes) -> bytes:
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()

    send = post or default_post

    def call(system: str, user: str) -> str:
        body = {"model": model, "stream": False, "options": {"temperature": 0.2},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        return json.loads(send(base + "/api/chat", json.dumps(body).encode()))["message"]["content"]

    return call


def main(argv: list[str] | None = None) -> int:
    from .news_briefing import FixtureSource, GoogleNewsSource

    p = argparse.ArgumentParser(prog="news_delivery", description="구독자별 뉴스 브리핑 · 부정 기사 알림을 보냅니다.")
    p.add_argument("--data-dir", required=True, type=Path, help="시스템 data 폴더 (news_subscribers.json이 있는 곳)")
    p.add_argument("--mode", choices=["briefing", "alerts"], default="briefing")
    p.add_argument("--subscriber", help="한 사람만 (id 또는 이메일)")
    p.add_argument("--period", choices=list(PERIODS), help="구독자 설정 대신 일간/주간")
    p.add_argument("--dry-run", action="store_true", help="보내지 않고 화면에 출력")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--watch", type=int, metavar="MINUTES", help="MINUTES마다 반복 (alerts 감시용)")
    a = p.parse_args(argv)
    load_env_file(Path(__file__).resolve().parents[3] / ".env")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if a.dry_run:
        printer = PrintSender()
        senders = {"telegram": printer, "email": printer}
    else:
        senders, missing = senders_from_env()
        for m in missing:
            print("설정 안 됨 →", m)
        if not senders:
            if not a.watch:
                print("보낼 채널이 없습니다. backend/.env를 채우거나 --dry-run으로 확인하세요.")
                return 1
            print("보낼 채널이 없어 화면(로그)에만 출력합니다.")
            printer = PrintSender()
            senders = {"telegram": printer, "email": printer}
    sources = [GoogleNewsSource(), FixtureSource(a.data_dir / "news.json")]
    llm = None if a.no_llm else cli_llm()
    days = PERIODS[a.period] if a.period else None
    while True:
        report = run_delivery(a.data_dir, a.mode, sources=sources, senders=senders, llm=llm, who=a.subscriber, days=days)
        if not a.watch:
            failed = any(r.get("failed") or any(x["failed"] for x in r.get("results", [])) for r in report)
            return 1 if failed else 0
        print(f"{a.watch}분 뒤 다시 확인합니다 (멈추려면 Ctrl+C)")
        time.sleep(a.watch * 60)


if __name__ == "__main__":
    raise SystemExit(main())
