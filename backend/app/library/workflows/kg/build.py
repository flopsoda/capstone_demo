"""Enterprise KG command line: the first build, a catch-up sync, or re-resolving with the current resolver.

    python -m app.library.workflows.kg.build --meeting-schema kelec --news-schema kelec_news \
        --data app/systems/k_electronics/data [--budget 50] [--no-model] [--rebuild-only]

--data is the system's data folder: its meetings.json and news.json seed the stores the way the system's app does
(an empty store gets them on first use) and its companies.json gives the seed entities.

The same KGStore.sync the background job runs (MEETINGS_DATABASE_URL, NEWS_DATABASE_URL; the system's chat model
for news extraction). --rebuild-only re-resolves the raw layer without reading the sources or calling a model.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..meeting_store import MeetingStore
from ..news.store import NewsStore
from .store import KGStore


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--meeting-schema", required=True, help="the system's schema (kelec | grp)")
    parser.add_argument("--news-schema", required=True, help="the system's news schema (kelec_news | grp_news)")
    parser.add_argument("--data", type=Path, required=True, help="the system's data folder (meetings.json, news.json, companies.json)")
    parser.add_argument("--budget", type=int, default=None, help="news extraction calls this run (default KG_LLM_BUDGET)")
    parser.add_argument("--no-model", action="store_true", help="structural only; events wait for a model")
    parser.add_argument("--rebuild-only", action="store_true", help="re-resolve the raw layer only")
    args = parser.parse_args(argv)
    from ...tools.correct_transcript import ask_model  # also loads backend/.env (runtime.llm)

    kg = KGStore(MeetingStore(args.meeting_schema, seed=args.data / "meetings.json"),
                 NewsStore(args.news_schema, seed=args.data / "news.json"), aliases=args.data / "companies.json")
    if args.rebuild_only:
        print(json.dumps(kg.rebuild(), ensure_ascii=False))
        return
    print(json.dumps(kg.sync(None if args.no_model else ask_model, budget=args.budget), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
