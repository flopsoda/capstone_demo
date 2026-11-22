"""K Electronics backend — FastAPI app over this system's agents.

Each run endpoint streams the agent's lifecycle events as NDJSON (one JSON
object per line) while the agent executes; see app/runtime/events.py for the
shapes. Started with `uvicorn app.main:app` (app/main.py re-exports this app).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ...library.tools.correct_transcript import ask_model
from ...library.workflows import meeting_report
from ...library.workflows.advisory.delivery import report_pdf
from ...library.workflows.jobs import start_meeting_jobs
from ...library.workflows.meeting_store import viewer_for
from ...runtime.api import ndjson_run, run_identity, shutdown_signal, sse_events
from ...runtime.events import new_run
from ...runtime.schemas import A2ARequest, ReportRequest, RunRequest
from . import ADVISORY_STORE, KG_STORE, MEETING_STORE, SYSTEM
from .agents.advisory import run_advisory_agent
from .agents.k_super import run_k_super
from .agents.meeting import run_meeting_agent
from .agents.news import run_news_agent

app = FastAPI(title="K Electronics agent system", version="0.5.0")
# Observer streams end on shutdown so uvicorn --reload can restart; the meeting store's mail /
# reminder jobs start with the app (only when MEETINGS_BACKGROUND_JOBS=1) and stop with it.
STOP = shutdown_signal(app, on_start=lambda stop: start_meeting_jobs(MEETING_STORE, stop, kg=lambda: KG_STORE.watch_pass(ask_model)))


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


def _report(body: ReportRequest, origin: str) -> Response:
    out = report_pdf(ADVISORY_STORE, viewer_for(run_identity(body)["principal"], origin), body.id)
    if "error" in out:
        return JSONResponse({"error": out["error"]}, status_code=out["status"])
    return Response(out["content"], media_type="application/pdf",
                    headers={"Content-Disposition": meeting_report.content_disposition(out["filename"]), "Cache-Control": "no-store"})


@app.post("/api/a2a/reports", response_model=None)
def a2a_report(body: ReportRequest) -> Response:
    """투자심의 보고서 PDF of a kept Advisory judgement, for another agent system that relays it to its user
    (the A2A surface: the forwarded, signed user is checked against this system's access rule)."""
    return _report(body, "a2a")


@app.post("/api/reports", response_model=None)
def advisory_report(body: ReportRequest) -> Response:
    """The same PDF for this system's own users (K UI)."""
    return _report(body, "direct")
