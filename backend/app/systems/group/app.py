"""Group backend — FastAPI app over the Group agent system.

Run beside the K Electronics backend: `uvicorn app.systems.group.app:app --port 8001`.
Run endpoints stream lifecycle events as NDJSON; /api/events is the observer channel.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse

from ...library.workflows import meeting_desk, meeting_report
from ...library.workflows.jobs import start_meeting_jobs
from ...library.workflows.meeting_store import StoreUnavailable, Viewer, viewer_for
from ...runtime.api import ndjson_run, run_identity, shutdown_signal, sse_events
from ...runtime.events import new_run
from ...runtime.schemas import Identified, RunRequest
from . import MEETING_STORE, SYSTEM
from .agents.advisory import run_advisory_agent
from .agents.group_super import run_group_super
from .agents.meeting import run_meeting_agent
from .agents.news import run_news_agent

app = FastAPI(title="Group agent system", version="0.5.0")
# Observer streams end on shutdown so uvicorn --reload can restart; the meeting store's mail /
# reminder jobs start with the app (only when MEETINGS_BACKGROUND_JOBS=1) and stop with it.
STOP = shutdown_signal(app, on_start=lambda stop: start_meeting_jobs(MEETING_STORE, stop))


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
    return ndjson_run(lambda: run_group_super(body.request, invocation="direct"), new_run(system=SYSTEM, root_agent="group_super", **run_identity(body)))


@app.post("/api/agents/meeting/run")
def meeting_run(body: RunRequest) -> StreamingResponse:
    """Direct user → Group Meeting Agent (group-level meeting records). Streams events."""
    return ndjson_run(lambda: run_meeting_agent(body.request, invocation="direct"), new_run(system=SYSTEM, root_agent="meeting", **run_identity(body)))


@app.post("/api/agents/news/run")
def news_run(body: RunRequest) -> StreamingResponse:
    """Direct user → Group News Agent. Streams events."""
    return ndjson_run(lambda: run_news_agent(body.request, invocation="direct"), new_run(system=SYSTEM, root_agent="news", **run_identity(body)))


@app.post("/api/agents/advisory/run")
def advisory_run(body: RunRequest) -> StreamingResponse:
    """Direct user → Group Advisory Agent (group-level acquisition targets). Streams events."""
    return ndjson_run(lambda: run_advisory_agent(body.request, invocation="direct"), new_run(system=SYSTEM, root_agent="advisory", **run_identity(body)))


# ---------------------------------------------------------------------------
# Meeting desk: the screen where people read the meeting store and press review / done / resend
# themselves (no agent in between). Same principal handling as runs; see library/workflows/meeting_desk.
# ---------------------------------------------------------------------------


class DeskRequest(Identified):
    action: str = "list"
    view: str = "mine"
    meeting_id: str | None = None
    mail_ids: list[str] | None = None
    email: str | None = None
    note: str | None = None
    reviews: list[dict[str, Any]] | None = None
    item_id: str | None = None
    status: str | None = None
    title: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    location: str | None = None
    attendees: list[str] | None = None
    agenda: list[str] | None = None
    classification: int | None = None
    kind: str | None = None  # report: minutes(회의록) · report(회의 결과 보고서)
    owner: str | None = None  # items: 담당 지정
    due: str | None = None  # items: 기한 지정 (YYYY-MM-DD)


def _desk(body: DeskRequest, call: Callable[[Viewer], dict[str, Any]]) -> dict[str, Any]:
    viewer = viewer_for(run_identity(body)["principal"], "direct")
    try:
        return call(viewer)
    except StoreUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.post("/api/desk/overview")
def desk_overview(body: DeskRequest) -> dict[str, Any]:
    return _desk(body, lambda v: meeting_desk.overview(store=MEETING_STORE, viewer=v))


@app.post("/api/desk/mail")
def desk_mail(body: DeskRequest) -> dict[str, Any]:
    return _desk(body, lambda v: meeting_desk.mail(store=MEETING_STORE, viewer=v, action=body.action, mail_ids=body.mail_ids,
                                                   email=body.email, note=body.note))


@app.post("/api/desk/flags")
def desk_flags(body: DeskRequest) -> dict[str, Any]:
    if body.action == "review":
        return _desk(body, lambda v: meeting_desk.review_flags(store=MEETING_STORE, viewer=v, meeting_id=body.meeting_id,
                                                               reviews=body.reviews))
    return _desk(body, lambda v: meeting_desk.open_flags(store=MEETING_STORE, viewer=v))


@app.post("/api/desk/items")
def desk_items(body: DeskRequest) -> dict[str, Any]:
    if body.item_id:
        return _desk(body, lambda v: meeting_desk.update_item(store=MEETING_STORE, viewer=v, item_id=body.item_id,
                                                              status=body.status, note=body.note, owner=body.owner, due=body.due))
    return _desk(body, lambda v: meeting_desk.items(store=MEETING_STORE, viewer=v, view=body.view))


@app.post("/api/desk/schedule")
def desk_schedule(body: DeskRequest) -> dict[str, Any]:
    return _desk(body, lambda v: meeting_desk.schedule(
        store=MEETING_STORE, viewer=v, action=body.action, meeting_id=body.meeting_id, title=body.title, starts_at=body.starts_at,
        ends_at=body.ends_at, location=body.location, attendees=body.attendees, agenda=body.agenda, classification=body.classification))


@app.post("/api/desk/meeting")
def desk_meeting(body: DeskRequest) -> dict[str, Any]:
    return _desk(body, lambda v: meeting_desk.meeting(store=MEETING_STORE, viewer=v, meeting_id=body.meeting_id))


@app.post("/api/desk/report", response_model=None)
def desk_report(body: DeskRequest) -> Response | dict[str, Any]:
    """회의록 · 회의 결과 보고서 PDF (an {error} JSON like the other desk calls when there is none to give)."""
    out = _desk(body, lambda v: meeting_report.meeting_pdf(store=MEETING_STORE, viewer=v, meeting_id=body.meeting_id, kind=body.kind))
    if "error" in out:
        return out
    return Response(out["content"], media_type="application/pdf",
                    headers={"Content-Disposition": meeting_report.content_disposition(out["filename"])})
