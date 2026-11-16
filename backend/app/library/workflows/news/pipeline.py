"""The news pipeline — one ingest() for the Tools (a request's collection) and for scheduled
collection (collect.py), and gather() for a request on top of it:

    feeds     poll the publisher feeds not polled within FEED_TTL_MINUTES (simple TTL freshness)
      → discover : every feed item is stored once (normalized URL → stable id), feed data only
      → fetch    : pages of the articles that matter now (a request: the ones whose feed title or
                   description names every query term; scheduled: any), robots.txt respected,
                   inside a time / count budget — what does not fit stays pending for later
      → extract  : article text (trafilatura + clean-up), content level, page metadata
      → store    : the better content replaces the feed data; a failure records why
      → embed    : vectors for articles that have none yet, or whose text got better (embed.py),
                   newest first, inside the budget — the rest wait for the next run

    gather   ingest for the query, then search everything stored (earlier requests and scheduled
             runs included) by keywords and by meaning, fused (reciprocal rank fusion) →
             duplicates out → mentions of the query terms → events (persisted, an article keeps
             its event) → the articles and their events for a view to analyze. Without the
             embedding model the search is keywords only, and says so.

A failing feed or page never stops the run: it is counted and recorded.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from . import cluster
from .extract import SNIPPET_CHARS, extract
from .embed import Embedder, EmbedUnavailable, article_text
from .feeds import KST, Feed, parse_feed
from .fetch import Fetcher, FetchError
from .store import NewsStore, article_id, squeeze
from .text import terms as query_terms

FEED_TTL_MINUTES = 15.0
LOOKBACK_DAYS = 120  # how far back a request looks by default; NEWS_LOOKBACK_DAYS changes it


def lookback_days() -> int:
    """Default (and longest) window of a request, in days."""
    try:
        return max(1, int(os.environ.get("NEWS_LOOKBACK_DAYS", "") or LOOKBACK_DAYS))
    except ValueError:
        return LOOKBACK_DAYS

Log = Callable[[str, dict], None]


@dataclass(frozen=True)
class Budget:
    seconds: float = 12.0  # wall clock for polling + page fetches
    bodies: int = 15  # article pages fetched at most
    workers: int = 8  # concurrent connections (one per host at a time: fetch.py)
    embeds: int = 64  # articles embedded at most
    embed_seconds: float = 3.0  # wall clock for embedding, after the fetches


REQUEST_BUDGET = Budget()
SCHEDULED_BUDGET = Budget(seconds=600.0, bodies=400, workers=8, embeds=20000, embed_seconds=600.0)


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
                "published": i.published or now, "feed_url": feed.url, "discovered": now} for i in items))
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


def embed_new(store: NewsStore, embedder: Embedder, *, since: datetime | None, budget: Budget) -> dict[str, Any]:
    """Embeds articles that have no vector from this model yet (or a vector of poorer text), newest first."""
    deadline = time.monotonic() + budget.embed_seconds
    rows = store.unembedded(embedder.model, since=since, limit=budget.embeds)
    done, error = 0, None
    for i in range(0, len(rows), BATCH_EMBED):
        if time.monotonic() >= deadline:
            break
        batch = rows[i : i + BATCH_EMBED]
        try:
            vectors = embedder.embed_documents([article_text(r) for r in batch])
        except EmbedUnavailable as e:
            error = str(e)
            break
        done += store.save_embeddings(embedder.model, ((r["id"], r["content_level"], v) for r, v in zip(batch, vectors)))
    return {"embedded": done, "embed_waiting": len(rows) - done, **({"embed_error": error} if error else {})}


BATCH_EMBED = 32


def ingest(store: NewsStore, feeds: list[Feed], fetcher: Fetcher, *, terms: list[str] | None = None, days: int = 7,
           budget: Budget = REQUEST_BUDGET, trigger: str = "request", query: str | None = None,
           now: datetime | None = None, log: Log | None = None, embedder: Embedder | None = None,
           query_vector: list[float] | None = None) -> dict[str, Any]:
    """Polls due feeds, fetches the pages that matter and embeds what is new (see the module docstring).
    A request's pages: those whose feed text names every term, then (with `query_vector`) the closest in
    meaning among the ones embedded from their feed text. Returns run stats."""
    log = log or (lambda stage, detail: None)
    now = now or datetime.now(KST)
    started = time.monotonic()
    deadline = started + budget.seconds
    # Feeds get most of the budget only when they are due; pages get whatever is left.
    stats = poll_feeds(store, feeds, fetcher, now=now, deadline=started + budget.seconds * 0.6, workers=budget.workers)
    log("collect", {k: stats[k] for k in ("feeds_polled", "feeds_fresh", "discovered")})
    since = now - timedelta(days=days)
    candidates = store.pending(terms=terms, since=since, limit=budget.bodies)
    if query_vector is not None and embedder is not None and len(candidates) < budget.bodies:
        seen = {c["id"] for c in candidates}
        similar = store.pending_similar(query_vector, embedder.model, since=since, limit=budget.bodies, floor=embedder.cutoff[0])
        candidates += [c for c in similar if c["id"] not in seen][: budget.bodies - len(candidates)]
    stats.update(fetch_bodies(store, fetcher, candidates, now=now, deadline=deadline, workers=budget.workers))
    log("fetch", {k: stats[k] for k in ("pages_tried", "pages_unfinished", "page_results")})
    if embedder is not None:
        stats.update(embed_new(store, embedder, since=None if trigger == "scheduled" else since, budget=budget))
        log("embed", {k: stats[k] for k in ("embedded", "embed_waiting")})
    stats["seconds"] = round(time.monotonic() - started, 2)
    store.record_run(trigger=trigger, query=query, started=now, finished=datetime.now(KST), stats=stats)
    return stats


LOW_COVERAGE = 3  # fewer stored matches than this: say the result is thin
LEAD_CHARS = 200


def lead_of(row: dict[str, Any]) -> str:
    return (row.get("body") or row.get("summary") or "")[:LEAD_CHARS]


RRF_K = 60  # reciprocal rank fusion: score = sum of 1 / (RRF_K + rank) over the lists an article is in
SEMANTIC_HITS = 30  # closest articles in meaning taken from the store per request


def fuse(keyword: list[dict[str, Any]], semantic: list[dict[str, Any]], *, widen: bool) -> list[dict[str, Any]]:
    """One ranking from the keyword hits (newest first) and the semantic hits (closest first). With
    `widen`, semantic hits the keywords missed join the result; without, they only reorder it."""
    score: dict[str, float] = {}
    rows: dict[str, dict[str, Any]] = {}
    for rank, r in enumerate(keyword, 1):
        score[r["id"]] = 1 / (RRF_K + rank)
        rows[r["id"]] = r
    for rank, r in enumerate(semantic, 1):
        if r["id"] not in rows:
            if not widen:
                continue
            rows[r["id"]], score[r["id"]] = r, 0.0
        rows[r["id"]]["similarity"] = r["similarity"]
        score[r["id"]] += 1 / (RRF_K + rank)
    return sorted(rows.values(), key=lambda r: -score[r["id"]])


def gather(store: NewsStore, feeds: list[Feed], fetcher: Fetcher, query: str, *, meaning: str | None = None,
           widen: bool = True, embedder: Embedder | None = None, days: int = 7, budget: Budget = REQUEST_BUDGET,
           now: datetime | None = None, log: Log | None = None) -> dict[str, Any]:
    """One request's news: collect what is due, then search everything stored by `query`'s words and by
    the meaning of `meaning` (default: the query). `widen=False` keeps only articles that name the
    query (a company's risks): meaning then only orders them. Returns {"articles": rows (best first,
    duplicates out, each with event_id), "duplicates", "freshness", "coverage"}."""
    store.migrate()  # schema and seed ready before the request's clock starts: registered articles are not "new now"
    now = now or datetime.now(KST)
    terms = query_terms(query)
    vector, search_error = None, None
    if embedder is not None:
        try:
            vector = embedder.embed_query(meaning or query)
        except EmbedUnavailable as e:
            search_error = str(e)
    stats = ingest(store, feeds, fetcher, terms=terms, days=days, budget=budget, query=query, now=now, log=log,
                   embedder=embedder if vector is not None else None, query_vector=vector if widen else None)
    window = {"since": now - timedelta(days=days), "until": now + timedelta(hours=1)}
    keyword = store.search(terms, **window)
    semantic: list[dict[str, Any]] = []
    if vector is not None:
        floor, margin = embedder.cutoff
        semantic = store.semantic_search(vector, embedder.model, limit=SEMANTIC_HITS, floor=floor, **window)
        if semantic:
            best = semantic[0]["similarity"]
            semantic = [r for r in semantic if r["similarity"] >= best - margin]
    rows = fuse(keyword, semantic, widen=widen)
    keyword_ids = {r["id"] for r in keyword}
    items = [cluster.Item(r["id"], r["title"], r["outlet"], r["published_at"], r["url"] or "", lead_of(r), r["body_hash"],
                          str(r["event_id"]) if r["event_id"] else None, has_body=bool(r["body"])) for r in rows]
    kept, dropped = cluster.dedupe(items)
    keep = {i.id for i in kept}
    rows = [r for r in rows if r["id"] in keep]
    copies = cluster.syndicated(kept)
    store.mark_syndicated(copies.items())
    for r in rows:
        r["syndicated_from"] = r.get("syndicated_from") or copies.get(r["id"])

    mention_rows = []
    for r in rows:
        title, text = squeeze(r["title"]), squeeze(f"{r['title']} {r['summary']} {r['body']}")
        counts = ((t, text.count(squeeze(t))) for t in terms)
        mention_rows.extend((r["id"], t, n, squeeze(t) in title) for t, n in counts if n)  # names the text really has
    store.save_mentions(mention_rows)

    assignment = cluster.assign(kept, terms)
    event_of = store.assign_events(assignment, {r["id"]: r["title"] for r in rows})
    for r in rows:
        r["event_id"] = event_of[r["id"]]

    fetched_now = sum(1 for r in rows if r["fetch_status"] == "ok" and r["fetched_at"] is not None and r["fetched_at"] >= now)
    discovered_now = sum(1 for r in rows if r["discovered_at"] >= now)
    unfinished = stats["feeds_unfinished"] + stats["pages_unfinished"]
    status = "low" if len(rows) < LOW_COVERAGE else "partial" if unfinished else "ok"
    search = {"mode": "hybrid" if vector is not None else "keyword", "keyword_matches": len(keyword),
              "semantic_matches": len(semantic), "semantic_only": sum(1 for r in rows if r["id"] not in keyword_ids)}
    if embedder is not None:
        search["model"] = embedder.model
        search["embedded_now"] = stats.get("embedded", 0)
    if search_error:
        search["error"] = search_error
    log and log("search", {"stored_matches": len(rows), "events": len(set(event_of.values())), **search})
    return {
        "articles": rows,
        "duplicates": len(dropped),
        "freshness": {
            "feed_ttl_minutes": FEED_TTL_MINUTES,
            "feeds_polled_now": stats["feeds_polled"],
            "feeds_fresh": stats["feeds_fresh"],
            "feeds_failed": len(stats["feeds_failed"]),
            "discovered_now": discovered_now,
            "fetched_now": fetched_now,
            "reused_from_store": len(rows) - discovered_now,
        },
        "coverage": {"status": status, "stored_matches": len(rows), "unfinished": unfinished, "search": search,
                     "note": "언론사 RSS로 수집된 기사와 저장소에 등록된 기사만 검색됩니다 (검색 API 미사용)."
                             + ("" if vector is not None else " 임베딩 모델을 쓸 수 없어 키워드로만 찾았습니다." if search_error else "")},
    }
