"""Duplicates, syndication and events: which articles are copies, which ran another outlet's story,
and which report the same matter.

    dedupe  the same link, the same text (body hash), or a near-identical headline from the same
            outlet is one article. Other outlets' reports of a story stay: they are its coverage.
    syndicated  another outlet running the same text (body hash, or nearly the same opening of
            the body), or a wire story credited in its first line ("(서울=연합뉴스)"): coverage counts
            the story once, not once per outlet that ran it.
    assign  each article joins the event of an earlier article that reports the same matter
            (headline bigram similarity with the query words removed, or the same opening text,
            within EVENT_WINDOW_DAYS of it), else starts a new event. Articles that already have
            an event keep it, so an event id never changes once given out (store.assign_events).

The demo fixture labels its articles (`hint`): the same labeled risk about the same companies is
one matter even when the headlines differ (a lawsuit, then an injunction in it).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .text import similarity

SAME_STORY = 0.25  # headline similarity (query words removed) for two reports of one story
SAME_LEAD = 0.45  # opening-text similarity: a wire story rewritten lightly by another outlet
SAME_ARTICLE = 0.6  # same outlet, this similar a headline: one article seen twice
SAME_COPY = 0.85  # different outlets, this similar an opening of the body: one ran the other's text
EVENT_WINDOW_DAYS = 3
# A wire story credits its agency in the first line: "(서울=연합뉴스) 홍길동 기자 =", "[서울=뉴시스]".
_WIRE = re.compile(r"[(\[](?:[가-힣A-Za-z]+\s*=\s*)?(연합뉴스|뉴시스|뉴스1)[)\]]")


def wire_agency(lead: str) -> str | None:
    m = _WIRE.search(lead[:80])
    return m.group(1) if m else None


@dataclass
class Item:
    """What clustering needs to know about an article."""

    id: str
    title: str
    outlet: str
    published: datetime
    url: str = ""
    lead: str = ""  # opening text: the body's first ~200 characters, or the description
    body_hash: str | None = None
    group: str | None = None  # event already given (live) or None
    hint: dict[str, Any] | None = field(default=None, repr=False)
    has_body: bool = False


def dedupe(items: list[Item]) -> tuple[list[Item], dict[str, str]]:
    """Returns (kept items, dropped id → kept id). Oldest copy is kept."""
    kept: list[Item] = []
    dropped: dict[str, str] = {}
    for a in sorted(items, key=lambda a: a.published):
        twin = next((k for k in kept if (a.url and a.url == k.url) or (a.body_hash and a.body_hash == k.body_hash and a.outlet == k.outlet)
                     or (a.outlet == k.outlet and similarity(a.title, k.title) >= SAME_ARTICLE)), None)
        if twin:
            dropped[a.id] = twin.id
        else:
            kept.append(a)
    return kept, dropped


def syndicated(items: list[Item]) -> dict[str, str]:
    """copy id → original id: a later article from another outlet with the same body, or with nearly
    the same opening text, ran the earlier one's story."""
    out: dict[str, str] = {}
    seen: list[Item] = []
    for a in sorted(items, key=lambda a: a.published):
        original = next((o for o in seen if o.outlet != a.outlet and (
            (a.body_hash and a.body_hash == o.body_hash)
            or (a.has_body and o.has_body and similarity(a.lead, o.lead) >= SAME_COPY))), None)
        if original:
            out[a.id] = out.get(original.id, original.id)
        seen.append(a)
    return out


def independent_outlet(outlet: str, lead: str) -> str:
    """Whose reporting this is: the wire agency a story credits, else the outlet itself."""
    agency = wire_agency(lead)
    return agency if agency and agency not in outlet else outlet


def _same_labeled_matter(a: Item, b: Item) -> bool:
    ha, hb = a.hint or {}, b.hint or {}
    ra, rb = ha.get("risk"), hb.get("risk")
    if ha.get("issue") and hb.get("issue"):
        return ha["issue"] == hb["issue"]
    return bool(ra and rb and ra["category"] == rb["category"] and set(ha.get("companies", [])) == set(hb.get("companies", [])))


def same_matter(a: Item, b: Item, remove: list[str]) -> bool:
    if a.hint is not None and b.hint is not None:  # labeled demo articles: the labels decide
        return _same_labeled_matter(a, b) or similarity(a.title, b.title, remove) >= SAME_STORY
    if abs((a.published - b.published).days) > EVENT_WINDOW_DAYS:
        return False
    if a.body_hash and a.body_hash == b.body_hash:
        return True
    if similarity(a.title, b.title, remove) >= SAME_STORY:
        return True
    return bool(a.lead and b.lead and similarity(a.lead, b.lead, remove) >= SAME_LEAD)


def assign(items: list[Item], remove: list[str]) -> dict[str, str]:
    """article id → group key. Items with a group keep it; an item joining nobody gets its own id as key
    (store.assign_events turns those keys into new event ids)."""
    out: dict[str, str] = {}
    members: dict[str, list[Item]] = {}
    for a in sorted(items, key=lambda a: a.published):
        if a.group:
            out[a.id] = a.group
            members.setdefault(a.group, []).append(a)
    for a in sorted(items, key=lambda a: a.published):
        if a.group:
            continue
        best, best_score = None, 0.0
        for key, group in members.items():
            score = max((similarity(a.title, m.title, remove) + (1.0 if same_matter(a, m, remove) else 0.0)) for m in group)
            if score >= 1.0 and score > best_score:
                best, best_score = key, score
        key = best or a.id
        out[a.id] = key
        members.setdefault(key, []).append(a)
    return out


def groups(items: list[Item], assignment: dict[str, str]) -> list[list[Item]]:
    by: dict[str, list[Item]] = {}
    for a in items:
        by.setdefault(assignment[a.id], []).append(a)
    return list(by.values())
