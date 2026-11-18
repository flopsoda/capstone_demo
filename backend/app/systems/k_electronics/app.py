"""K Electronics backend — FastAPI app over this system's agents.

Each run endpoint streams the agent's lifecycle events as NDJSON (one JSON
object per line) while the agent executes; see app/runtime/events.py for the
shapes. Started with `uvicorn app.main:app` (app/main.py re-exports this app).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from ...library.workflows.jobs import start_meeting_jobs
from ...runtime.api import ndjson_run, run_identity, shutdown_signal, sse_events
from ...runtime.events import new_run
from ...runtime.schemas import A2ARequest, RunRequest
from . import MEETING_STORE, SYSTEM
from .agents.advisory import run_advisory_agent
from .agents.k_super import run_k_super
from .agents.meeting import run_meeting_agent
from .agents.news import run_news_agent

app = FastAPI(title="K Electronics agent system", version="0.5.0")
# Observer streams end on shutdown so uvicorn --reload can restart; the meeting store's mail /
# reminder jobs start with the app (only when MEETINGS_BACKGROUND_JOBS=1) and stop with it.
STOP = shutdown_signal(app, on_start=lambda stop: start_meeting_jobs(MEETING_STORE, stop))


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "system": SYSTEM}


@app.get("/api/events")
def events() -> StreamingResponse:
    """Observer channel: every lifecycle event this system emits, as Server-Sent Events."""
    return sse_events(stop=STOP)


@app.post("/api/a2a/run")
def a2a_run(body: A2ARequest) -> StreamingResponse:
    """A2A inbound surface: another agent system's Super Agent hands K Super a request.
    K Super delegates to its specialists and answers; the answer is agent_end(k_super).response.
    The stream also carries K's lifecycle, which the caller may relay for observation but
    must not feed to its agent as business data."""
    run = new_run(
        system=SYSTEM, root_agent="k_super", origin="a2a", trace_id=body.trace_id, caller=body.caller, request=body.request,
        **run_identity(body),
    )
    return ndjson_run(lambda: run_k_super(body.request, invocation="a2a"), run)


@app.post("/api/agents/k-super/run")
def k_super_direct_run(body: RunRequest) -> StreamingResponse:
    """Direct user → K Super (sidebar chat). Same agent; it may answer without delegating. Streams events."""
    return ndjson_run(lambda: run_k_super(body.request, invocation="direct"), new_run(system=SYSTEM, root_agent="k_super", **run_identity(body)))


@app.post("/api/agents/meeting/run")
def meeting_run(body: RunRequest) -> StreamingResponse:
    """Direct user → Meeting Agent (sidebar chat), same agent core as under delegation. Streams events."""
    return ndjson_run(lambda: run_meeting_agent(body.request, invocation="direct"), new_run(system=SYSTEM, root_agent="meeting", **run_identity(body)))


@app.post("/api/agents/news/run")
def news_run(body: RunRequest) -> StreamingResponse:
    """Direct user → News Agent (sidebar chat). Streams events."""
    return ndjson_run(lambda: run_news_agent(body.request, invocation="direct"), new_run(system=SYSTEM, root_agent="news", **run_identity(body)))


@app.post("/api/agents/advisory/run")
def advisory_run(body: RunRequest) -> StreamingResponse:
    """Direct user → Advisory Agent (sidebar chat). Streams events."""
    return ndjson_run(lambda: run_advisory_agent(body.request, invocation="direct"), new_run(system=SYSTEM, root_agent="advisory", **run_identity(body)))
