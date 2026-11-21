"""Enterprise KG command line: the first build, a catch-up sync, or re-resolving with the current resolver.

    python -m app.library.workflows.kg.build --meeting-schema kelec --news-schema kelec_news \
        --aliases app/systems/k_electronics/data/companies.json [--budget 50] [--no-model] [--rebuild-only]

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
    parser.add_argument("--aliases", type=Path, help="the system's companies.json (seed entities)")
    parser.add_argument("--budget", type=int, default=None, help="news extraction calls this run (default KG_LLM_BUDGET)")
    parser.add_argument("--no-model", action="store_true", help="structural only; events wait for a model")
    parser.add_argument("--rebuild-only", action="store_true", help="re-resolve the raw layer only")
    args = parser.parse_args(argv)
    kg = KGStore(MeetingStore(args.meeting_schema), NewsStore(args.news_schema), aliases=args.aliases)
    if args.rebuild_only:
        print(json.dumps(kg.rebuild(), ensure_ascii=False))
        return
    ask = None
    if not args.no_model:
        from ...tools.correct_transcript import ask_model  # the system's chat model (runtime.llm)

        ask = ask_model
    print(json.dumps(kg.sync(ask, budget=args.budget), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
