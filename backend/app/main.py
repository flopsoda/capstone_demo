"""Minimal API layer between the React demo and the LangGraph agents.

Each run endpoint streams the agent's lifecycle events as NDJSON (one JSON
object per line) while the agent executes; see app/events.py for the shapes.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from .agents.k_super import run_k_super
from .agents.meeting import run_meeting_agent
from .agents.news import run_news_agent
from .events import stream_run
from .schemas import RunRequest

app = FastAPI(title="capstone agents backend", version="0.3.0")


def _ndjson(fn: Callable[[], Any]) -> StreamingResponse:
    def lines() -> Iterator[str]:
        for event in stream_run(fn):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/k-super/run")
def k_super_run(body: RunRequest) -> StreamingResponse:
    """Scenario Steps 3–4: K Super receives the A2A request and delegates to each specialist. Streams events."""
    return _ndjson(lambda: run_k_super(body.request))


@app.post("/api/agents/meeting/run")
def meeting_run(body: RunRequest) -> StreamingResponse:
    """Direct user → Meeting Agent (sidebar chat), same agent core as Step 3. Streams events."""
    return _ndjson(lambda: run_meeting_agent(body.request, require_tool=False))


@app.post("/api/agents/news/run")
def news_run(body: RunRequest) -> StreamingResponse:
    """Direct user → News Agent (sidebar chat), same agent core as Step 4. Streams events."""
    return _ndjson(lambda: run_news_agent(body.request, require_tool=False))
