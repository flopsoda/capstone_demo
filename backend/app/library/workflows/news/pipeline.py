"""The news pipeline — one ingest() for the Tools (a request's collection) and for scheduled
collection (collect.py):

    feeds     poll the publisher feeds not polled within FEED_TTL_MINUTES (simple TTL freshness)
      → discover : every feed item is stored once (normalized URL → stable id), feed data only
      → fetch    : pages of the articles that matter now (a request: the ones whose feed title or
                   description names every query term; scheduled: any), robots.txt respected,
                   inside a time / count budget — what does not fit stays pending for later
      → extract  : article text (trafilatura + clean-up), content level, page metadata
      → store    : the better content replaces the feed data; a failure records why

A failing feed or page never stops the run: it is counted and recorded.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .extract import SNIPPET_CHARS, extract
from .feeds import KST, Feed, parse_feed
from .fetch import Fetcher, FetchError
from .store import NewsStore, article_id

FEED_TTL_MINUTES = 15.0

Log = Callable[[str, dict], None]


@dataclass(frozen=True)
class Budget:
    seconds: float = 12.0  # wall clock for polling + page fetches
    bodies: int = 15  # article pages fetched at most
    workers: int = 8  # concurrent connections (one per host at a time: fetch.py)


REQUEST_BUDGET = Budget()
SCHEDULED_BUDGET = Budget(seconds=600.0, bodies=400, workers=8)


def _run_until(tasks: list[Callable[[], Any]], workers: int, deadline: float) -> tuple[list[Any], int]:
    """Runs tasks concurrently until the deadline. Returns (results of finished tasks, tasks left undone)."""
    results: list[Any] = []
    if not tasks:
        return results, 0
    pool = ThreadPoolExecutor(max_workers=workers)
    pending = {pool.submit(t) for t in tasks}
    try:
        while pending:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            done, pending = wait(pending, timeout=left, return_when=FIRST_COMPLETED)
            results.extend(f.result() for f in done)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return results, len(pending)


def poll_feeds(store: NewsStore, feeds: list[Feed], fetcher: Fetcher, *, now: datetime, deadline: float,
               workers: int, ttl_minutes: float = FEED_TTL_MINUTES) -> dict[str, Any]:
    due_urls = store.feeds_due([f.url for f in feeds], ttl_minutes, now)
    due = [f for f in feeds if f.url in due_urls]

    def poll(feed: Feed) -> tuple[Feed, str, list]:
        try:
            page = fetcher.get(feed.url)
            return feed, "ok", parse_feed(page.body, feed)
        except FetchError as e:
            return feed, e.status, []
        except Exception as e:  # a feed that is not a feed
            return feed, f"error:{type(e).__name__}", []

    results, unfinished = _run_until([lambda f=f: poll(f) for f in due], workers, deadline)
    discovered = 0
    failed = []
    with store.connect() as conn:
        for feed, status, items in results:
            store.record_feed(conn, url=feed.url, domain=feed.domain, outlet=feed.outlet, kind=feed.kind,
                              status=status, count=len(items) if status == "ok" else None, now=now)
            if status != "ok":
                failed.append({"feed": feed.url, "status": status})
                continue
            discovered += store.add_discovered(conn, ({
                "id": article_id(i.url), "url": i.url, "domain": i.domain, "outlet": feed.outlet, "title": i.title,
                "summary": i.summary, "content_level": "snippet" if len(i.summary) >= SNIPPET_CHARS else "title",
                "published": i.published or now, "feed_url": feed.url} for i in items))
    return {"feeds_total": len(feeds), "feeds_polled": len(results), "feeds_fresh": len(feeds) - len(due),
            "feeds_unfinished": unfinished, "feeds_failed": failed, "discovered": discovered}


def fetch_bodies(store: NewsStore, fetcher: Fetcher, candidates: list[dict[str, Any]], *, now: datetime,
                 deadline: float, workers: int) -> dict[str, Any]:
    def one(c: dict[str, Any]) -> str:
        try:
            page = fetcher.get(c["url"])
        except FetchError as e:
            store.save_fetch(c["id"], status=e.status, now=now)
            return e.status
        x = extract(page.body, page.url, c.get("summary", ""))
        store.save_fetch(c["id"], status="ok", now=now, content_level=x.content_level, body=x.body,
                         body_hash=x.body_hash, title=x.title, summary=x.description, canonical_url=x.canonical_url,
                         published=x.published, usage_notice=x.usage_notice)
        return x.content_level

    results, unfinished = _run_until([lambda c=c: one(c) for c in candidates], workers, deadline)
    counts: dict[str, int] = {}
    for r in results:
        counts[r] = counts.get(r, 0) + 1
    return {"pages_tried": len(results), "pages_unfinished": unfinished, "page_results": counts}


def ingest(store: NewsStore, feeds: list[Feed], fetcher: Fetcher, *, terms: list[str] | None = None, days: int = 7,
           budget: Budget = REQUEST_BUDGET, trigger: str = "request", query: str | None = None,
           now: datetime | None = None, log: Log | None = None) -> dict[str, Any]:
    """Polls due feeds and fetches the pages that matter (see the module docstring). Returns run stats."""
    log = log or (lambda stage, detail: None)
    now = now or datetime.now(KST)
    started = time.monotonic()
    deadline = started + budget.seconds
    # Feeds get most of the budget only when they are due; pages get whatever is left.
    stats = poll_feeds(store, feeds, fetcher, now=now, deadline=started + budget.seconds * 0.6, workers=budget.workers)
    log("collect", {k: stats[k] for k in ("feeds_polled", "feeds_fresh", "discovered")})
    candidates = store.pending(terms=terms, since=now - timedelta(days=days), limit=budget.bodies)
    stats.update(fetch_bodies(store, fetcher, candidates, now=now, deadline=deadline, workers=budget.workers))
    stats["seconds"] = round(time.monotonic() - started, 2)
    log("fetch", {k: stats[k] for k in ("pages_tried", "pages_unfinished", "page_results")})
    store.record_run(trigger=trigger, query=query, started=now, finished=datetime.now(KST), stats=stats)
    return stats
