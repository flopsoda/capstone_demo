"""HTTP helpers shared by the agent systems' FastAPI apps."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

from fastapi.responses import StreamingResponse

from .events import RunContext, stream_run


def ndjson_run(fn: Callable[[], Any], run: RunContext) -> StreamingResponse:
    """Runs `fn` as `run` and streams its lifecycle events as NDJSON (one JSON object per line)."""

    def lines() -> Iterator[str]:
        for event in stream_run(fn, run=run):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
