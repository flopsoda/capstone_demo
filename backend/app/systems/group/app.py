"""Group backend — FastAPI app over the Group agent system.

Run beside the K Electronics backend: `uvicorn app.systems.group.app:app --port 8001`.
Run endpoints stream lifecycle events as NDJSON; /api/events is the observer channel.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from ...library.workflows import decision_watch, mail_actions, meeting_control, meeting_desk, meeting_report
from ...library.tools.correct_transcript import ask_model
from ...library.workflows.advisory.delivery import report_pdf
from ...library.workflows.jobs import start_meeting_jobs
from ...library.workflows.meeting_store import StoreUnavailable, Viewer, viewer_for
from ...runtime.api import ndjson_run, run_identity, shutdown_signal, sse_events
from ...runtime.events import new_run
from ...runtime.schemas import Identified, ReportRequest, RunRequest
from . import ADVISORY_STORE, KG_STORE, MEETING_STORE, SYSTEM
from .agents.advisory import run_advisory_agent
from .agents.group_super import run_group_super
from .agents.meeting import run_meeting_agent
from .agents.news import run_news_agent
from .tools.correct_transcript import TRANSCRIPT_CORRECTION
from .tools import request_k_electronics_review as k_a2a
from .tools.run_decision_review import DECISION_WATCH

app = FastAPI(title="Group agent system", version="0.5.0")
# Observer streams end on shutdown so uvicorn --reload can restart; the meeting store's mail /
# reminder jobs and the decision watch (news against the premises of the decisions in force) start
# with the app (only when MEETINGS_BACKGROUND_JOBS=1) and stop with it.
STOP = shutdown_signal(app, on_start=lambda stop: start_meeting_jobs(
    MEETING_STORE, stop, watch=DECISION_WATCH.scan, kg=lambda: KG_STORE.watch_pass(ask_model)))


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
    """Direct user → Group Meeting Agent (group-level meeting records). Streams events.
    A recording's transcript in the request is corrected first (tools/correct_transcript)."""
    def run() -> str:
        prepared = TRANSCRIPT_CORRECTION.prepare(body.request)
        answer = run_meeting_agent(prepared, invocation="direct")
        TRANSCRIPT_CORRECTION.check_saved(prepared)  # a recording's minutes must really be saved
        return answer

    return ndjson_run(run, new_run(system=SYSTEM, root_agent="meeting", **run_identity(body)))


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
    query: str | None = None  # minutes: 검색어
    offset: int | None = None  # minutes: 몇 번째부터
    due: str | None = None  # items: 기한 지정 (YYYY-MM-DD)
    code: str | None = None  # checkin: 출석 확인 코드 (CHK-…)
    token: str | None = None  # checkin join: QR의 30초 코드
    voice_consent: bool | None = None  # checkin join: 이 회의 녹음에서 목소리를 익혀도 되는지 (None = 그대로)
    source_name: str | None = None  # checkin open: 함께 올린 녹음 파일 이름
    speaker: str | None = None  # attendance assign: 목소리 라벨 ('화자 C')
    person: dict[str, Any] | None = None  # attendance: {name, email, user_id} (gateway가 회사 계정으로 확인)
    signal_id: str | None = None  # watch: 전제를 흔든 신호 (SIG-…)
    carry_over_from: str | None = None  # schedule: 안건 · 미결 쟁점을 이어 받을 이전 회의 id


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
    if body.item_id and body.action == "split":
        return _desk(body, lambda v: meeting_desk.split_item(store=MEETING_STORE, viewer=v, item_id=body.item_id))
    if body.item_id:
        return _desk(body, lambda v: meeting_desk.update_item(store=MEETING_STORE, viewer=v, item_id=body.item_id,
                                                              status=body.status, note=body.note, owner=body.owner, due=body.due))
    return _desk(body, lambda v: meeting_desk.items(store=MEETING_STORE, viewer=v, view=body.view))


@app.post("/api/desk/schedule")
def desk_schedule(body: DeskRequest) -> dict[str, Any]:
    return _desk(body, lambda v: meeting_desk.schedule(
        store=MEETING_STORE, viewer=v, action=body.action, meeting_id=body.meeting_id, title=body.title, starts_at=body.starts_at,
        ends_at=body.ends_at, location=body.location, attendees=body.attendees, agenda=body.agenda, classification=body.classification,
        carry_over_from=body.carry_over_from))


@app.post("/api/desk/meeting")
def desk_meeting(body: DeskRequest) -> dict[str, Any]:
    return _desk(body, lambda v: meeting_desk.meeting(store=MEETING_STORE, viewer=v, meeting_id=body.meeting_id))


@app.post("/api/desk/checkin")
def desk_checkin(body: DeskRequest) -> dict[str, Any]:
    return _desk(body, lambda v: meeting_desk.checkin(store=MEETING_STORE, viewer=v, action=body.action, code=body.code, token=body.token,
                                                      voice_consent=body.voice_consent, source_name=body.source_name))


@app.post("/api/desk/attendance")
def desk_attendance(body: DeskRequest) -> dict[str, Any]:
    return _desk(body, lambda v: meeting_desk.attendance(store=MEETING_STORE, viewer=v, action=body.action, meeting_id=body.meeting_id,
                                                         speaker=body.speaker, person=body.person))


@app.post("/api/desk/watch")
def desk_watch(body: DeskRequest) -> dict[str, Any]:
    """전제가 흔들린 결정: 목록 · 문제없음(dismiss) · 재검토로 넘김(reviewed)."""
    return _desk(body, lambda v: decision_watch.set_signal(store=MEETING_STORE, viewer=v, action=body.action,
                                                           signal_id=body.signal_id, note=body.note))


@app.post("/api/desk/finalize")
def desk_finalize(body: DeskRequest) -> dict[str, Any]:
    """회의 관제의 '확정 · 메일 발송': 저장된 초안을 그대로 확정 (확정 권한은 회의록 확정과 같음)."""
    return _desk(body, lambda v: meeting_desk.finalize(store=MEETING_STORE, viewer=v, meeting_id=body.meeting_id))


@app.post("/api/desk/control")
def desk_control(body: DeskRequest) -> dict[str, Any]:
    """회의 관제: 주제마다 지금 상태와, 회의 Agent가 본 것 · 한 일."""
    return _desk(body, lambda v: meeting_control.control(store=MEETING_STORE, viewer=v))


@app.post("/api/desk/minutes")
def desk_minutes(body: DeskRequest) -> dict[str, Any]:
    return _desk(body, lambda v: meeting_desk.minutes_list(store=MEETING_STORE, viewer=v, query=body.query, status=body.status,
                                                           offset=body.offset))


class MailActionRequest(BaseModel):
    token: str
    status: str | None = None
    note: str | None = None


@app.post("/api/mail-action")
def mail_action(body: MailActionRequest) -> dict[str, Any]:
    """업무 배정 · 기한 알림 메일의 버튼 (gateway /meetings/act/…): 로그인 대신 메일의 서명된 링크로 담당자를 정한다.
    status 없이 부르면 그 업무를 보여 주고, 있으면 진행 상황을 남긴다 (library/workflows/mail_actions)."""
    try:
        return mail_actions.act(store=MEETING_STORE, token=body.token, status=body.status, note=body.note)
    except StoreUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.post("/api/desk/report", response_model=None)
def desk_report(body: DeskRequest) -> Response | dict[str, Any]:
    """회의록 · 회의 결과 보고서 PDF (an {error} JSON like the other desk calls when there is none to give)."""
    out = _desk(body, lambda v: meeting_report.meeting_pdf(store=MEETING_STORE, viewer=v, meeting_id=body.meeting_id, kind=body.kind))
    if "error" in out:
        return out
    return Response(out["content"], media_type="application/pdf",
                    headers={"Content-Disposition": meeting_report.content_disposition(out["filename"])})


def _pdf(content: bytes, disposition: str) -> Response:
    return Response(content, media_type="application/pdf", headers={"Content-Disposition": disposition, "Cache-Control": "no-store"})


@app.post("/api/reports", response_model=None)
def advisory_report(body: ReportRequest) -> Response:
    """투자심의 보고서 PDF of a kept Advisory judgement that an agent answer referred to (its `artifacts`).
    This system's own judgements are drawn from its store; K전자's are fetched from K's A2A surface over HTTP with the
    same signed user, so K applies its own access rule (the systems never read each other's stores)."""
    identity = run_identity(body)
    if body.system in (None, "", SYSTEM):
        out = report_pdf(ADVISORY_STORE, viewer_for(identity["principal"], "direct"), body.id)
        if "error" in out:
            return JSONResponse({"error": out["error"]}, status_code=out["status"])
        return _pdf(out["content"], meeting_report.content_disposition(out["filename"]))
    if body.system != k_a2a.PEER_SYSTEM:
        return JSONResponse({"error": f"알 수 없는 system: {body.system}"}, status_code=404)
    url = k_a2a.k_electronics_a2a_url().rsplit("/run", 1)[0] + "/reports"
    payload: dict[str, Any] = {"id": body.id}
    if identity["principal"] is not None:
        payload["principal"] = identity["principal"].model_dump()
    if identity["principal_token"]:
        payload["principal_token"] = identity["principal_token"]
    try:
        with k_a2a.make_client() as client:  # the same client as the A2A Tool (tests reach K in-process)
            res = client.post(url, json=payload)
    except httpx.HTTPError as e:
        return JSONResponse({"error": f"K전자 Agent System에 연결할 수 없습니다: {type(e).__name__}"}, status_code=502)
    if res.status_code == 200 and res.headers.get("content-type", "").startswith("application/pdf"):
        return _pdf(res.content, res.headers.get("content-disposition", "attachment"))
    try:
        message = res.json().get("error") or res.json().get("detail")
    except ValueError:
        message = None
    return JSONResponse({"error": message or f"K전자가 보고서를 주지 않았습니다 (HTTP {res.status_code})"},
                        status_code=res.status_code if res.status_code in (401, 403, 404, 503) else 502)
