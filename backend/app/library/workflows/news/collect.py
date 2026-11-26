"""Scheduled collection — the same ingest() the Tools run, over every feed, with a bigger budget.
Run it from cron / the OS scheduler, or keep it running with --watch:

    cd backend
    .venv/bin/python -m app.library.workflows.news.collect --schema kelec_news            # K Electronics
    .venv/bin/python -m app.library.workflows.news.collect --schema grp_news --watch 30   # Group, every 30 min

Articles are stored in the system's news schema (NEWS_DATABASE_URL, else MEETINGS_DATABASE_URL in
backend/.env) and embedded for semantic search (embed.py; skipped when the model is off or down).
Requests then find them in the store: collection is not tied to any query, and the events and
analyses are made when a request asks about a subject. `--embed-only` embeds what the store holds
without polling (e.g. once after the first start, for the articles registered from data/news.json).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .embed import default_embedder
from .feeds import DEFAULT_FEEDS, load_feeds
from .fetch import Fetcher
from .pipeline import SCHEDULED_BUDGET, Budget, embed_new, ingest
from .store import NewsStore


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="news.collect", description="언론사 RSS · 뉴스 sitemap에서 기사를 모아 system의 뉴스 저장소에 쌓습니다.")
    p.add_argument("--schema", required=True, help="system의 뉴스 schema (K전자 kelec_news, 그룹 grp_news)")
    p.add_argument("--feeds", type=Path, default=DEFAULT_FEEDS, help="피드 목록 JSON (기본: news/feeds.json)")
    p.add_argument("--days", type=int, default=3, help="며칠 전 기사까지 본문을 가져올지 (기본 3)")
    p.add_argument("--pages", type=int, default=SCHEDULED_BUDGET.bodies, help="한 번에 가져올 기사 본문 수 상한")
    p.add_argument("--seconds", type=float, default=SCHEDULED_BUDGET.seconds, help="한 번 실행의 시간 상한(초)")
    p.add_argument("--watch", type=float, default=0, help="N분마다 반복 (0이면 한 번만)")
    p.add_argument("--embed-only", action="store_true", help="수집 없이 저장된 기사 중 임베딩이 없는 것만 임베딩")
    a = p.parse_args(argv)
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[4] / ".env")
    except ImportError:
        pass
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    store, feeds, fetcher, embedder = NewsStore(a.schema), load_feeds(a.feeds), Fetcher(), default_embedder()
    budget = Budget(seconds=a.seconds, bodies=a.pages, workers=SCHEDULED_BUDGET.workers, embeds=SCHEDULED_BUDGET.embeds,
                    embed_seconds=SCHEDULED_BUDGET.embed_seconds)
    if a.embed_only:
        if embedder is None:
            print("[embed] NEWS_EMBED_MODEL=none: nothing to do", flush=True)
            return 0
        stats = embed_new(store, embedder, since=None, budget=budget)
        print(f"[done] {json.dumps(stats, ensure_ascii=False)}", flush=True)
        return 1 if stats.get("embed_error") else 0
    while True:
        stats = ingest(store, feeds, fetcher, terms=None, days=a.days, budget=budget, trigger="scheduled", embedder=embedder,
                       log=lambda stage, detail: print(f"[{stage}] {json.dumps(detail, ensure_ascii=False)}", flush=True))
        keys = ("discovered", "pages_tried", "page_results", "embedded", "embed_waiting", "embed_error", "seconds")
        print(f"[done] {json.dumps({k: stats[k] for k in keys if k in stats}, ensure_ascii=False)}", flush=True)
        if not a.watch:
            return 0
        time.sleep(a.watch * 60)


if __name__ == "__main__":
    raise SystemExit(main())
