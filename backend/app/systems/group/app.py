"""Group backend — FastAPI app over the Group agent system.

Run beside the K Electronics backend: `uvicorn app.systems.group.app:app --port 8001`.
Run endpoints stream lifecycle events as NDJSON; /api/events is the observer channel.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from ...runtime.api import ndjson_run, shutdown_signal, sse_events
from ...runtime.events import new_run
from ...runtime.schemas import RunRequest
from . import SYSTEM
from .agents.advisory import run_advisory_agent
from .agents.group_super import run_group_super
from .agents.meeting import run_meeting_agent
from .agents.news import run_news_agent

app = FastAPI(title="Group agent system", version="0.5.0")
STOP = shutdown_signal(app)  # observer streams end on shutdown so uvicorn --reload can restart


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "system": SYSTEM}


@app.get("/api/events")
def events() -> StreamingResponse:
    """Observer channel: every lifecycle event this system emits (incl. events relayed from a peer), as SSE."""
    return sse_events(stop=STOP)


@app.post("/api/agents/group-super/run")
def group_super_run(body: RunRequest) -> StreamingResponse:
    """User → Group Super. Delegates to the Group's specialists or asks K Electronics over A2A, as it judges. Streams events."""
    return ndjson_run(lambda: run_group_super(body.request, invocation="direct"), new_run(system=SYSTEM, root_agent="group_super"))


@app.post("/api/agents/meeting/run")
def meeting_run(body: RunRequest) -> StreamingResponse:
    """Direct user → Group Meeting Agent (group-level meeting records). Streams events."""
    return ndjson_run(lambda: run_meeting_agent(body.request, invocation="direct"), new_run(system=SYSTEM, root_agent="meeting"))


@app.post("/api/agents/news/run")
def news_run(body: RunRequest) -> StreamingResponse:
    """Direct user → Group News Agent. Streams events."""
    return ndjson_run(lambda: run_news_agent(body.request, invocation="direct"), new_run(system=SYSTEM, root_agent="news"))


@app.post("/api/agents/advisory/run")
def advisory_run(body: RunRequest) -> StreamingResponse:
    """Direct user → Group Advisory Agent (group-level acquisition targets). Streams events."""
    return ndjson_run(lambda: run_advisory_agent(body.request, invocation="direct"), new_run(system=SYSTEM, root_agent="advisory"))
