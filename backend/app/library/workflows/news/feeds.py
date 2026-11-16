"""Feeds: which publisher feeds a system collects from, and how a feed becomes items.

A feed is a publisher's own RSS 2.0 / Atom feed or news sitemap (standard formats, one parser
for every outlet — no site-specific code). The list lives in a JSON file a system binds
(default: feeds.json next to this module):

    {"feeds": [{"outlet": "연합뉴스", "url": "https://www.yna.co.kr/rss/news.xml"},
               {"outlet": "파이낸셜뉴스", "url": "https://…/sitemap/news/latest.xml", "kind": "sitemap"}]}

Feeds only carry the latest items (hours to days). Older news is what the database already
holds from earlier polls (request-time or scheduled): see pipeline.py.
"""

from __future__ import annotations

import email.utils
import html
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9), "KST")
DEFAULT_FEEDS = Path(__file__).with_name("feeds.json")

_ATOM = "{http://www.w3.org/2005/Atom}"
_SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_NEWS = "{http://www.google.com/schemas/sitemap-news/0.9}"
_TAG = re.compile(r"<[^>]+>")
_DECL = re.compile(rb"^\s*<\?xml[^>]*\?>", re.S)
_ENC = re.compile(rb"encoding=[\"']([A-Za-z0-9_\-]+)[\"']")
# Query parameters that only track the click, never identify the article.
_TRACKING = re.compile(r"^(utm_[a-z]+|fbclid|gclid|cmpid|ref|from|sns|rss|cid|plink|cooper|wt_mc)$", re.I)


@dataclass(frozen=True)
class Feed:
    outlet: str
    url: str
    kind: str = "rss"  # rss (RSS 2.0 / Atom) | sitemap (news sitemap)

    @property
    def domain(self) -> str:
        return outlet_domain(self.url)


@dataclass
class FeedItem:
    url: str  # normalized: tracking parameters and fragment removed
    title: str
    summary: str  # the feed's own description (may be empty)
    published: datetime | None
    outlet: str
    domain: str
    feed_url: str


def load_feeds(path: Path = DEFAULT_FEEDS) -> list[Feed]:
    rows = json.loads(path.read_text(encoding="utf-8"))["feeds"]
    return [Feed(r["outlet"], r["url"], r.get("kind", "rss")) for r in rows]


def outlet_domain(url: str) -> str:
    host = urllib.parse.urlsplit(url).hostname or ""
    for prefix in ("www.", "m.", "rss.", "feeds.", "news.", "biz.", "view."):
        if host.startswith(prefix) and host.count(".") >= 2:
            host = host[len(prefix):]
    return host


def normalize_url(url: str) -> str:
    """The article's identity: scheme/host lower-cased, fragment and tracking parameters dropped."""
    parts = urllib.parse.urlsplit(url.strip())
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING.match(k)]
    path = parts.path or "/"
    return urllib.parse.urlunsplit((parts.scheme.lower() or "https", parts.netloc.lower(), path, urllib.parse.urlencode(query), ""))


def clean_text(text: str | None) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", html.unescape(text or ""))).split())


def parse_date(value: str | None) -> datetime | None:
    """RFC 822 (RSS), ISO 8601 (Atom, sitemaps) or "YYYY-MM-DD HH:MM[:SS]"; naive times are Korean time."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            m = re.match(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?", value)
            if not m:
                return None
            y, mo, d, h, mi, s = (int(x) if x else 0 for x in m.groups())
            try:
                dt = datetime(y, mo, d, h, mi, s)
            except ValueError:
                return None
    return dt if dt.tzinfo else dt.replace(tzinfo=KST)


def _utf8(body: bytes) -> bytes:
    """expat knows few encodings (no EUC-KR / CP949): decode by the declaration, hand it UTF-8."""
    head = body[:200]
    m = _ENC.search(head)
    enc = m.group(1).decode().lower() if m else "utf-8"
    if enc in ("utf-8", "utf8"):
        return body
    try:
        text = body.decode("cp949" if enc in ("euc-kr", "euckr", "ks_c_5601-1987") else enc, errors="replace")
    except LookupError:
        return body
    return _DECL.sub(b"", text.encode("utf-8"), count=1)


def _text(el: ET.Element | None, *names: str) -> str:
    if el is None:
        return ""
    for n in names:
        found = el.find(n)
        if found is not None and (found.text or "").strip():
            return found.text
    return ""


def _items_xml(root: ET.Element) -> list[tuple[str, str, str, str]]:
    """(link, title, summary, date) from RSS 2.0, Atom or a news sitemap."""
    out = []
    for it in root.iter("item"):
        out.append((_text(it, "link", "guid"), _text(it, "title"), _text(it, "description"),
                    _text(it, "pubDate", "{http://purl.org/dc/elements/1.1/}date")))
    for e in root.iter(f"{_ATOM}entry"):
        link = next((l.get("href", "") for l in e.findall(f"{_ATOM}link") if l.get("rel", "alternate") == "alternate"), "")
        out.append((link, _text(e, f"{_ATOM}title"), _text(e, f"{_ATOM}summary", f"{_ATOM}content"),
                    _text(e, f"{_ATOM}published", f"{_ATOM}updated")))
    for u in root.iter(f"{_SM}url"):
        news = u.find(f"{_NEWS}news")
        out.append((_text(u, f"{_SM}loc"), _text(news, f"{_NEWS}title"), "",
                    _text(news, f"{_NEWS}publication_date") or _text(u, f"{_SM}lastmod")))
    return out


_ITEM = re.compile(rb"<item[\s>].*?</item>", re.S | re.I)


def _field(block: str, name: str) -> str:
    m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", block, re.S | re.I)
    if not m:
        return ""
    v = m.group(1).strip()
    return v[9:-3] if v.startswith("<![CDATA[") and v.endswith("]]>") else v


def _items_lenient(body: bytes) -> list[tuple[str, str, str, str]]:
    """A feed that is not well-formed XML (a stray '&', a broken entity): read its <item>s one by one."""
    out = []
    for raw in _ITEM.findall(body):
        block = raw.decode("utf-8", errors="replace")
        out.append((_field(block, "link") or _field(block, "guid"), _field(block, "title"),
                    _field(block, "description"), _field(block, "pubDate") or _field(block, "dc:date")))
    return out


def parse_feed(body: bytes, feed: Feed) -> list[FeedItem]:
    body = _utf8(body)
    try:
        rows = _items_xml(ET.fromstring(body))
    except ET.ParseError:
        rows = _items_lenient(body)
        if not rows:
            raise
    items, seen = [], set()
    for link, title, summary, published in rows:
        link, title = clean_text(link), clean_text(title)
        if not link.startswith(("http://", "https://")) or not title:
            continue
        url = normalize_url(link)
        if url in seen:
            continue
        seen.add(url)
        summary = clean_text(summary)
        items.append(FeedItem(url, title, "" if summary == title else summary, parse_date(clean_text(published)),
                              feed.outlet, outlet_domain(url) or feed.domain, feed.url))
    return items
