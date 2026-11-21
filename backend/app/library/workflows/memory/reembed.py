"""Adds the configured embedding model's vectors to every kept conversation chunk that lacks one — after the model
changed (MEMORY_EMBED_PROVIDER / MEMORY_EMBED_MODEL), or for exchanges kept while the model was down.

    python -m app.library.workflows.memory.reembed --schema grp
"""

from __future__ import annotations

import argparse
import json

from ..meeting_store import MeetingStore
from .embedding import memory_embedder
from .store import MemoryStore

BATCH = 64


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--schema", required=True, help="the system's schema (grp | kelec)")
    args = parser.parse_args(argv)
    embedder = memory_embedder()
    if embedder is None:
        raise SystemExit("no embedding model is configured (MEMORY_EMBED_PROVIDER / MEMORY_EMBED_API_KEY)")
    store = MemoryStore(MeetingStore(args.schema))
    done = 0
    while rows := store.unembedded(embedder.name, BATCH):
        vectors = embedder.embed_documents([r["text"] for r in rows])
        done += store.save_embeddings(embedder.name, zip((r["id"] for r in rows), vectors))
    print(json.dumps({"model": embedder.name, "embedded": done}))


if __name__ == "__main__":
    main()
