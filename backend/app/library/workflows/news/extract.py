"""Article text from a publisher page: trafilatura's main-text extraction, then a few general
Korean-news clean-up rules (copyright lines, reporter e-mail, "많이 본 뉴스"…), then fallbacks.

    content_level  full     body >= 400 characters after clean-up
                   partial  150 – 399 characters (short reports, breaking news, partly rendered pages)
                   snippet  no usable body; the page's own description (og:description) or the feed's
                   title    nothing but the headline

Pages rendered by JavaScript, paywalls and blocked pages end up as snippet/title: the analysis
says which level it worked from, nothing is invented to fill the gap.

usage_notice: the page states an explicit "AI 학습·활용 금지" notice. The body is kept for
duplicate detection and grouping, but only the headline and lead go to the LLM (analysis.py).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

import trafilatura

from .feeds import parse_date

FULL_CHARS = 400
PARTIAL_CHARS = 150
SNIPPET_CHARS = 20

# A line that is page furniture, not reporting. Matched against whole lines after extraction.
_FURNITURE = re.compile(
    r"(무단\s*전재|재배포\s*금지|저작권자\s*[©ⓒ(]|copyright\s*(©|ⓒ|\(c\))|all rights reserved|"
    r"^[©ⓒ]|이 기사를 추천합니다|기사\s*더보기|많이 본 뉴스|관련\s*기사|구독\s*신청|"
    r"AI\s*핵심\s*요약|^[-–]?\s*ET Events|좋아요\s*\d|공유하기|기사제보|카카오톡\s*제보|"
    r"^\[?사진\s*=|^▶|^☞|^※\s*(이\s*기사|본\s*기사)|^AI\s*추천\s*뉴스$|공감언론|\[email\s*protected\]|"
    r"^등록\s*\d{4}[.-]\d{2}[.-]\d{2}|^(입력|수정|업데이트)\s*:?\s*\d{4}[.-]\d{2}[.-]\d{2})", re.I)
_EMAIL_LINE = re.compile(r"^[^\n]{0,40}([\w.+-]+@[\w-]+\.[\w.]+|기자\s+[\w.+-]+@\S*)\s*$")
# A publisher's machine summary above the article ends with this kind of line: summary and line go.
_SUMMARY_DISCLAIMER = re.compile(r"(기사\s*본문과\s*함께\s*읽어야|AI가\s*요약|요약은\s*AI|자동\s*요약)")
_NOT_LETTERS = re.compile(r"[^0-9A-Za-z가-힣]")
_TRAILING_BYLINE = re.compile(r"\s*[가-힣]{2,4}\s*(기자|특파원|에디터)\s*[\w.+-]+@\S*\s*$")
_USAGE_NOTICE = re.compile(r"AI\s*(학습|활용)[^\n]{0,15}금지|인공지능[^\n]{0,10}학습[^\n]{0,15}금지")
_SPACES = re.compile(r"[ \t ]+")


@dataclass
class Extracted:
    content_level: str
    body: str  # full / partial only; "" otherwise
    title: str | None
    description: str
    published: datetime | None
    canonical_url: str | None
    usage_notice: bool

    @property
    def body_hash(self) -> str | None:
        return body_hash(self.body)


def body_hash(body: str) -> str | None:
    """Same text, any whitespace: the same body (an exact copy or a wire story run unchanged)."""
    if not body:
        return None
    return hashlib.sha256(re.sub(r"\s+", "", body).encode("utf-8")).hexdigest()


def _same_text(a: str, b: str) -> bool:
    x, y = _NOT_LETTERS.sub("", a), _NOT_LETTERS.sub("", b)
    return bool(x) and bool(y) and (x in y or y in x) and min(len(x), len(y)) >= 8


def clean_body(text: str, title: str | None = None) -> str:
    raw_lines = text.splitlines()
    for i, raw in enumerate(raw_lines[:6]):  # a summary block the publisher put above the article
        if _SUMMARY_DISCLAIMER.search(raw):
            raw_lines = raw_lines[i + 1:]
            break
    lines = []
    for raw in raw_lines:
        line = _TRAILING_BYLINE.sub("", _SPACES.sub(" ", raw)).strip()
        if not line or _FURNITURE.search(line) or _EMAIL_LINE.match(line):
            continue
        if lines and line == lines[-1]:
            continue
        if not lines and title and _same_text(line, title) and len(line) <= len(title) + 12:
            continue  # the headline repeated (sometimes glued to a section word) at the top
        lines.append(line)
    # Trailing furniture without a marker: very short lines that are not a sentence.
    while len(lines) > 1 and len(lines[-1]) < 15 and not lines[-1].endswith((".", "다", "!", "?", "”", '"')):
        lines.pop()
    return "\n".join(lines)


def level_of(body: str, description: str) -> str:
    if len(body) >= FULL_CHARS:
        return "full"
    if len(body) >= PARTIAL_CHARS:
        return "partial"
    return "snippet" if len(description) >= SNIPPET_CHARS else "title"


def extract(html: bytes, url: str, feed_summary: str = "") -> Extracted:
    try:
        # deduplicate=False: trafilatura's dedup cache spans documents, so a wire story's paragraphs
        # seen in one outlet's page would vanish from the next outlet's.
        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False, deduplicate=False) or ""
        meta = trafilatura.extract_metadata(html, default_url=url)
    except Exception:  # a page trafilatura cannot read is just an empty page
        text, meta = "", None
    notice = bool(_USAGE_NOTICE.search(text))
    body = clean_body(text, meta.title if meta and meta.title else None)
    description = " ".join(((meta.description if meta else None) or "").split())
    if len(description) < len(feed_summary):
        description = feed_summary
    level = level_of(body, description)
    return Extracted(
        content_level=level,
        body=body if level in ("full", "partial") else "",
        title=(meta.title.strip() if meta and meta.title else None),
        description=description,
        published=parse_date(meta.date) if meta and meta.date else None,
        canonical_url=meta.url if meta and meta.url and meta.url.startswith("http") else None,
        usage_notice=notice,
    )
