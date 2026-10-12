"""K Electronics backend — FastAPI app over this system's agents.

Each run endpoint streams the agent's lifecycle events as NDJSON (one JSON
object per line) while the agent executes; see app/runtime/events.py for the
shapes. Started with `uvicorn app.main:app` (app/main.py re-exports this app).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from ...runtime.api import ndjson_run
from ...runtime.events import new_run
from ...runtime.schemas import RunRequest
from . import SYSTEM
from .agents.advisory import run_advisory_agent
from .agents.k_super import run_k_super
from .agents.meeting import run_meeting_agent
from .agents.news import run_news_agent

app = FastAPI(title="K Electronics agent system", version="0.5.0")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "system": SYSTEM}


@app.post("/api/k-super/run")
def k_super_run(body: RunRequest) -> StreamingResponse:
    """K Super receives a request from another agent system (A2A) and delegates to its specialists. Streams events."""
    return ndjson_run(lambda: run_k_super(body.request), new_run(system=SYSTEM, root_agent="k_super", origin="a2a"))


@app.post("/api/agents/meeting/run")
def meeting_run(body: RunRequest) -> StreamingResponse:
    """Direct user → Meeting Agent (sidebar chat), same agent core as under delegation. Streams events."""
    return ndjson_run(lambda: run_meeting_agent(body.request, invocation="direct"), new_run(system=SYSTEM, root_agent="meeting"))


@app.post("/api/agents/news/run")
def news_run(body: RunRequest) -> StreamingResponse:
    """Direct user → News Agent (sidebar chat). Streams events."""
    return ndjson_run(lambda: run_news_agent(body.request, invocation="direct"), new_run(system=SYSTEM, root_agent="news"))


@app.post("/api/agents/advisory/run")
def advisory_run(body: RunRequest) -> StreamingResponse:
    """Direct user → Advisory Agent (sidebar chat). Streams events."""
    return ndjson_run(lambda: run_advisory_agent(body.request, invocation="direct"), new_run(system=SYSTEM, root_agent="advisory"))
