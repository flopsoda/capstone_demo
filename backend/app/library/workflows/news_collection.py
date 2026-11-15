"""News collection — the fixed flow that fills a system's article store from real news.

    watchlist (companies.json + extra names)
      → fetch    : Naver News search API (NAVER_CLIENT_ID / NAVER_CLIENT_SECRET) or, without keys,
                   Google News RSS — no other dependency than the standard library
      → label    : per article, risk category · level · key facts (an LLM when one is configured,
                   keyword rules otherwise or when the model's answer is unusable)
      → store    : merged into <data dir>/collected/news.json in the same shape as data/news.json,
                   duplicates (same link, near-identical headline) skipped, old articles dropped

run_company_risk_news_analysis reads that file next to the fixture, so the Tool, the News Agent
and the UI do not change. The collected file holds real-world news: it is runtime data, kept out
of Git (.gitignore), never a fixture.

Run (backend/):
    .venv/bin/python -m app.library.workflows.news_collection --data-dir app/systems/k_electronics/data
    .venv/bin/python -m app.library.workflows.news_collection --data-dir app/systems/group/data --company 삼성전자=Samsung Electronics --no-llm

This module is not agent-visible and has no LangChain dependency (the CLI wraps the runtime's
chat model only when it runs).
"""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
COLLECTED_FILE = Path("collected") / "news.json"

Fetch = Callable[[str, dict[str, str]], bytes]  # (url, headers) -> body
LLM = Callable[[str, str], str]  # (system prompt, user prompt) -> text


# ── Labels ──────────────────────────────────────────────────────────────────

CATEGORY_LABELS = {
    "customer_concentration": "고객 집중도",
    "legal_dispute": "법적 분쟁",
    "key_personnel": "핵심인력",
    "labor_dispute": "노사 분쟁",
    "regulatory": "규제·제재",
    "market_reaction": "주가·주주 반응",
    "operations": "생산·공급 차질",
}

# Cues in an article's own text (not in a user's request: see company_risk_news_analysis).
ARTICLE_CUES: dict[str, tuple[str, ...]] = {
    "customer_concentration": ("최대 고객", "매출 비중", "고객 집중", "고객사 이탈", "공급업체 다변화", "주문 축소", "물량 축소", "거래 중단"),
    "legal_dispute": ("소송", "특허침해", "특허 침해", "가처분", "제소", "피소", "분쟁", "판결"),
    "key_personnel": ("사임", "퇴사", "이직", "핵심 인력", "핵심인력", "연구진 이탈", "인력 이탈", "경영진 교체"),
    "labor_dispute": ("노조", "파업", "임금 협상", "임금협상", "쟁의", "노사", "성과급 갈등", "성과급 논란", "성과급 반발"),
    "regulatory": ("규제", "제재", "과징금", "공정위", "금산법", "당국 조사", "수출 통제", "수출통제", "막히나", "인허가"),
    "market_reaction": ("주가 하락", "주가 급락", "급락", "흘러내", "빠졌", "등 돌", "약세", "실망", "비판", "주주 불만", "매도세"),
    "operations": ("감산", "생산 차질", "생산 중단", "가동 중단", "공급 부족", "수율 문제", "리콜", "가동률 하락"),
}
# A non-risk article counts as good news only with one of these; otherwise it is neutral and left out.
POSITIVE_CUES = ("수주", "흑자", "호실적", "최대 실적", "역대 최대", "신기록", "돌파", "증가", "1위", "연속", "채택", "출시",
                 "투자 유치", "승소", "인증", "급등", "공급 계약", "배당")
# Opinion pieces argue rather than report an event: at most LOW.
OPINION_CUES = ("칼럼", "사설", "기고", "시론", "오피니언", "[기자수첩]")
HIGH_CUES = ("대규모", "전면", "중단", "판매금지", "수입 금지", "형사", "기소", "패소", "잇단", "대거", "최대 규모")
LOW_CUES = ("가능성", "검토", "전망", "우려", "관측")

_TAG = re.compile(r"<[^>]+>")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class RawArticle:
    """One search hit before labeling."""

    title: str
    summary: str
    url: str
    publisher: str
    published: date
    company: str  # the watchlist name it was found for
    aliases: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return "LIVE-" + hashlib.sha1((self.url or self.title).encode()).hexdigest()[:12]


def _clean(text: str) -> str:
    return " ".join(html.unescape(_TAG.sub("", text or "")).split())


def _date(value: str) -> date:
    try:
        return email.utils.parsedate_to_datetime(value).astimezone(KST).date()
    except (TypeError, ValueError):
        return datetime.now(KST).date()


# ── Fetch ───────────────────────────────────────────────────────────────────

def http_fetch(url: str, headers: dict[str, str]) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 capstone-news-collector", **headers})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read()


def fetch_google_news(company: str, aliases: list[str], days: int, fetch: Fetch = http_fetch, limit: int = 30,
                      exact: bool = True) -> list[RawArticle]:
    """`exact`: names as quoted phrases (a company). False: a topic's words in any order ("HBM 시장 가격")."""
    names = " OR ".join(f'"{n}"' for n in [company, *aliases]) if exact else company
    q = urllib.parse.quote(f"{names} when:{max(1, days)}d")
    body = fetch(f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko", {})
    out = []
    for item in ET.fromstring(body).iter("item"):
        title, publisher = _clean(item.findtext("title", "")), _clean(item.findtext("source", ""))
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3]
        summary = _clean(item.findtext("description", ""))
        if summary.startswith(title):  # Google's description is the linked title + publisher: no body text
            summary = ""
        out.append(RawArticle(title, summary, item.findtext("link", "").strip(),
                              publisher or "Google 뉴스", _date(item.findtext("pubDate", "")), company, aliases))
        if len(out) >= limit:
            break
    return out


def fetch_naver_news(company: str, aliases: list[str], client_id: str, client_secret: str,
                     fetch: Fetch = http_fetch, limit: int = 30) -> list[RawArticle]:
    url = f"https://openapi.naver.com/v1/search/news.json?query={urllib.parse.quote(company)}&display={min(limit, 100)}&sort=date"
    items = json.loads(fetch(url, {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret})).get("items", [])
    out = []
    for it in items:
        link = it.get("originallink") or it.get("link", "")
        host = urllib.parse.urlparse(link).netloc.removeprefix("www.")
        out.append(RawArticle(_clean(it.get("title", "")), _clean(it.get("description", "")), link,
                              host or "네이버 뉴스", _date(it.get("pubDate", "")), company, aliases))
    return out


# ── Label ───────────────────────────────────────────────────────────────────

def _facts(summary: str, limit: int = 2) -> list[str]:
    sentences = [s.strip() for s in _SENTENCE.split(summary) if len(s.strip()) >= 8]
    return [s[:90] for s in sentences[:limit]]


def _named_in_title(raw: RawArticle) -> bool:
    title = raw.title.lower()
    return any(n.lower() in title for n in [raw.company, *raw.aliases])


def rule_label(raw: RawArticle) -> dict[str, Any]:
    """Keyword rules: the category with the most cues; HIGH / LOW from strong / hedging words.
    An article that does not name the company in its headline only mentions it in passing:
    neutral, never a risk or good news of that company."""
    if not _named_in_title(raw):
        return _record(raw, None, _facts(raw.summary) or [raw.title], "rule", "neutral")
    text = f"{raw.title} {raw.summary}"
    hits = {cat: sum(c in text for c in cues) for cat, cues in ARTICLE_CUES.items()}
    category = max(hits, key=hits.get)
    sentiment = "negative"
    if not hits[category]:
        risk = None
        sentiment = "positive" if any(c in text for c in POSITIVE_CUES) else "neutral"
    else:
        if any(c in raw.title for c in OPINION_CUES):
            level = "LOW"
        else:
            level = "HIGH" if any(c in text for c in HIGH_CUES) else "LOW" if any(c in text for c in LOW_CUES) else "MEDIUM"
        risk = {"category": category, "label": CATEGORY_LABELS[category], "level": level}
    return _record(raw, risk, _facts(raw.summary) or [raw.title], "rule", sentiment)


LABEL_SYSTEM = f"""너는 기업 뉴스를 리스크 관점으로 분류하는 분석가다. 기사 목록을 받아 JSON 배열만 출력한다. 설명은 쓰지 않는다.
각 원소: {{"id": 기사 id,
 "category": {", ".join(f'"{k}"({v})' for k, v in CATEGORY_LABELS.items())} 중 하나, 리스크가 아니면 null,
 "positive": category가 null일 때, 이 기업에 분명히 좋은 소식이면 true (실적 개선, 수주, 채택 등), 아니면 false,
 - 기업이 지나가듯 언급만 된 기사는 category null, positive false.
 "level": "HIGH" | "MEDIUM" | "LOW" (category가 null이면 생략),
 "key_facts": 기사에 실제로 적힌 사실 1~3개 (각 60자 이내)}}
- 기사에 없는 사실을 만들지 않는다. 추측성 보도("~할 가능성", "검토")와 칼럼·사설은 LOW."""


def llm_label(raws: list[RawArticle], llm: LLM, batch: int = 10) -> dict[str, dict[str, Any]]:
    """id → record for the articles the model labeled usably; the rest are left to the rules."""
    out: dict[str, dict[str, Any]] = {}
    by_id = {r.id: r for r in raws}
    for i in range(0, len(raws), batch):
        chunk = raws[i : i + batch]
        payload = [{"id": r.id, "company": r.company, "title": r.title, "summary": r.summary[:500]} for r in chunk]
        try:
            rows = parse_json_array(llm(LABEL_SYSTEM, json.dumps(payload, ensure_ascii=False)))
        except Exception:  # network, quota, model errors: fall back to the rules for this batch
            continue
        for row in rows:
            raw = by_id.get(row.get("id")) if isinstance(row, dict) else None
            if raw is None:
                continue
            category = row.get("category")
            if category is not None and category not in CATEGORY_LABELS:
                continue
            level = row.get("level")
            if category and level not in ("HIGH", "MEDIUM", "LOW"):
                continue
            facts = [str(f).strip()[:90] for f in row.get("key_facts") or [] if str(f).strip()][:3]
            risk = {"category": category, "label": CATEGORY_LABELS[category], "level": level} if category else None
            sentiment = "negative" if category else "positive" if row.get("positive") is True else "neutral"
            out[raw.id] = _record(raw, risk, facts or _facts(raw.summary) or [raw.title], "llm", sentiment)
    return out


def parse_json_array(text: str) -> list:
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _record(raw: RawArticle, risk: dict | None, facts: list[str], labeled_by: str, sentiment: str) -> dict[str, Any]:
    return {
        "id": raw.id,
        "date": raw.published.isoformat(),
        "source": raw.publisher,
        "headline": raw.title,
        "summary": raw.summary,
        "companies": list(dict.fromkeys([raw.company, *raw.aliases])),
        "risk": risk,
        "sentiment": sentiment,  # negative (a risk) | positive (good news) | neutral (neither: not shown)
        "key_facts": facts,
        "url": raw.url,
        "labeled_by": labeled_by,
    }


# ── Store ───────────────────────────────────────────────────────────────────

def _bigrams(text: str) -> set[str]:
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", text)
    return {t[i : i + 2] for i in range(len(t) - 1)}


def _same_story(a: str, b: str, threshold: float = 0.6) -> bool:
    x, y = _bigrams(a), _bigrams(b)
    return bool(x and y) and len(x & y) / len(x | y) >= threshold


def merge(existing: list[dict[str, Any]], new: Iterable[dict[str, Any]], *, today: date, keep_days: int) -> tuple[list[dict[str, Any]], int]:
    """Existing + new, minus duplicates and articles older than keep_days. Returns (articles, added).
    A duplicate is the same article (id, link) or a near-identical headline from the SAME outlet;
    other outlets' reports of the same story are kept, so the analysis can count the coverage."""
    cutoff = (today - timedelta(days=keep_days)).isoformat()
    kept = [a for a in existing if a["date"] >= cutoff]
    added = 0
    for a in new:
        if a["date"] < cutoff:
            continue
        if any(a["id"] == k["id"] or (a.get("url") and a.get("url") == k.get("url"))
               or (a.get("source") == k.get("source") and _same_story(a["headline"], k["headline"])) for k in kept):
            continue
        kept.append(a)
        added += 1
    kept.sort(key=lambda a: a["date"], reverse=True)
    return kept, added


def read_store(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["articles"]


def write_store(path: Path, articles: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"articles": articles}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)  # readers never see a half-written file


# ── The flow ────────────────────────────────────────────────────────────────

def watchlist_from(companies_path: Path) -> list[tuple[str, list[str]]]:
    """(name, aliases) for every company in a system's companies.json."""
    if not companies_path.exists():
        return []
    rows = json.loads(companies_path.read_text(encoding="utf-8"))["companies"]
    return [(c["name"], [a for a in c.get("aliases", []) if a != c["name"]]) for c in rows]


def collect(
    watchlist: list[tuple[str, list[str]]],
    store: Path,
    *,
    days: int = 7,
    keep_days: int = 90,
    llm: LLM | None = None,
    fetch: Fetch = http_fetch,
    naver: tuple[str, str] | None = None,
    today: date | None = None,
    log: Callable[[str], None] = lambda line: None,
) -> dict[str, Any]:
    """Fetch → label → store for every company on the watchlist. One company's failure does not stop the rest."""
    today = today or datetime.now(KST).date()
    since = today - timedelta(days=days)
    raws: list[RawArticle] = []
    failed: list[str] = []
    for name, aliases in watchlist:
        try:
            found = fetch_naver_news(name, aliases, *naver, fetch=fetch) if naver else fetch_google_news(name, aliases, days, fetch=fetch)
        except Exception as e:  # network, parsing: report and go on
            failed.append(name)
            log(f"[fetch] {name}: 실패 ({type(e).__name__})")
            continue
        found = [r for r in found if r.published >= since]
        log(f"[fetch] {name}: {len(found)}건 ({'naver' if naver else 'google_news'})")
        raws.extend(found)

    labeled = llm_label(raws, llm) if llm and raws else {}
    records = [labeled.get(r.id) or rule_label(r) for r in raws]
    by = {"llm": sum(r["labeled_by"] == "llm" for r in records), "rule": sum(r["labeled_by"] == "rule" for r in records)}
    log(f"[label] llm {by['llm']}건 · rule {by['rule']}건 · 리스크 {sum(r['risk'] is not None for r in records)}건")

    articles, added = merge(read_store(store), records, today=today, keep_days=keep_days)
    write_store(store, articles)
    log(f"[store] {store}: 새 기사 {added}건, 전체 {len(articles)}건")
    return {"fetched": len(raws), "added": added, "total": len(articles), "labeled_by": by, "failed": failed}


def _runtime_llm() -> LLM | None:
    """The backend's configured chat model, or None when it is not configured (rules then)."""
    try:
        from ...runtime.llm import LLMConfigError, get_chat_model
    except ImportError:
        return None
    try:
        model = get_chat_model()
    except LLMConfigError:
        return None

    def call(system: str, user: str) -> str:
        return model.invoke([("system", system), ("human", user)]).content

    return call


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="news_collection", description="관심 기업 뉴스를 모아 시스템의 기사 저장소에 쌓습니다.")
    p.add_argument("--data-dir", required=True, type=Path, help="시스템 data 폴더 (companies.json이 있는 곳)")
    p.add_argument("--company", action="append", default=[], help="추가로 모을 기업. 별칭은 = 뒤에 쉼표로 (예: 삼성전자=Samsung Electronics)")
    p.add_argument("--only", action="store_true", help="companies.json은 빼고 --company만 모음")
    p.add_argument("--days", type=int, default=7, help="며칠 전 기사까지 (기본 7)")
    p.add_argument("--keep-days", type=int, default=90, help="저장소에 남길 기간 (기본 90)")
    p.add_argument("--no-llm", action="store_true", help="LLM 없이 키워드 규칙으로만 분류")
    a = p.parse_args(argv)
    try:  # backend dependency; NAVER_* and LLM keys live in backend/.env. Without it: the environment only
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    except ImportError:
        pass
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    watchlist = [] if a.only else watchlist_from(a.data_dir / "companies.json")
    for spec in a.company:
        name, _, rest = spec.partition("=")
        watchlist.append((name.strip(), [x.strip() for x in rest.split(",") if x.strip()]))
    if not watchlist:
        p.error("모을 기업이 없습니다 (companies.json 또는 --company)")
    keys = (os.environ.get("NAVER_CLIENT_ID", ""), os.environ.get("NAVER_CLIENT_SECRET", ""))
    out = collect(
        watchlist, a.data_dir / COLLECTED_FILE, days=a.days, keep_days=a.keep_days,
        llm=None if a.no_llm else _runtime_llm(), naver=keys if all(keys) else None, log=print,
    )
    return 1 if out["failed"] and not out["fetched"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
