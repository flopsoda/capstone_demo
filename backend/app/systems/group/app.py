"""Group backend — FastAPI app over the Group agent system.

Run beside the K Electronics backend: `uvicorn app.systems.group.app:app --port 8001`.
Run endpoints stream lifecycle events as NDJSON; /api/events is the observer channel.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from ...runtime.api import ndjson_run, sse_events
from ...runtime.events import new_run
from ...runtime.schemas import RunRequest
from . import SYSTEM
from .agents.group_super import run_group_super

app = FastAPI(title="Group agent system", version="0.5.0")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "system": SYSTEM}


@app.get("/api/events")
def events() -> StreamingResponse:
    """Observer channel: every lifecycle event this system emits (incl. events relayed from a peer), as SSE."""
    return sse_events()


@app.post("/api/agents/group-super/run")
def group_super_run(body: RunRequest) -> StreamingResponse:
    """User → Group Super. Asks K Electronics over A2A when its review is needed. Streams events."""
    return ndjson_run(lambda: run_group_super(body.request, invocation="direct"), new_run(system=SYSTEM, root_agent="group_super"))
