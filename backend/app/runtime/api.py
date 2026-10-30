"""HTTP helpers shared by the agent systems' FastAPI apps."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from .events import HUB, EventHub, RunContext, stream_run
from .principal import PrincipalRejected, trusted_principal
from .schemas import Identified

SSE_KEEPALIVE_SECONDS = 5.0  # also how quickly an observer stream notices the app shutting down


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


def run_identity(body: Identified) -> dict[str, Any]:
    """`principal` / `principal_token` keyword arguments for new_run(), from a run (or other identified) request.
    A user named without a valid signature (when a signing key is configured) is refused with 401."""
    try:
        principal, token = trusted_principal(body.principal, body.principal_token)
    except PrincipalRejected as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    return {"principal": principal, "principal_token": token}


def sse_line(event: Any) -> str:
    """One Server-Sent Events message carrying the event as JSON."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def sse_events(hub: EventHub = HUB, stop: threading.Event | None = None) -> StreamingResponse:
    """Streams every event this process publishes, as text/event-stream, until the client
    disconnects or the app shuts down (`stop`). Observers (Demo Monitor, platform UIs) use
    this; it is not tied to the request that started a run, so runs started by another
    system's A2A request show too. Observers are expected to reconnect (EventSource does)."""

    def messages() -> Iterator[str]:
        yield ": connected\n\n"
        for event in hub.subscribe(timeout=SSE_KEEPALIVE_SECONDS):
            yield sse_line(event) if event is not None else ": keepalive\n\n"
            if stop is not None and stop.is_set():
                return

    return StreamingResponse(
        messages(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


def shutdown_signal(app: FastAPI, on_start: Callable[[threading.Event], Any] | None = None) -> threading.Event:
    """An Event set when `app` shuts down (lifespan). Long-lived observer streams check it
    so a graceful shutdown — e.g. uvicorn --reload — is not held open by them forever.
    `on_start(stop)` runs when the app starts (a system's background jobs, which end on `stop`)."""
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if on_start is not None:
            on_start(stop)
        try:
            yield
        finally:
            stop.set()

    app.router.lifespan_context = lifespan
    return stop
