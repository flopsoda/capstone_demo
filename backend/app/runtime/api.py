"""HTTP helpers shared by the agent systems' FastAPI apps."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

from fastapi.responses import StreamingResponse

from .events import HUB, EventHub, RunContext, stream_run

SSE_KEEPALIVE_SECONDS = 15.0


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


def sse_line(event: Any) -> str:
    """One Server-Sent Events message carrying the event as JSON."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def sse_events(hub: EventHub = HUB) -> StreamingResponse:
    """Streams every event this process publishes, as text/event-stream, until the client
    disconnects. Observers (Demo Monitor, platform UIs) use this; it is not tied to the
    request that started a run, so runs started by another system's A2A request show too."""

    def messages() -> Iterator[str]:
        yield ": connected\n\n"
        for event in hub.subscribe(timeout=SSE_KEEPALIVE_SECONDS):
            yield sse_line(event) if event is not None else ": keepalive\n\n"

    return StreamingResponse(
        messages(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
