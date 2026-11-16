"""회의록 · 회의 결과 보고서 PDF (library/workflows/meeting_report) and the desk download."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.library.workflows import meeting_report
from app.library.workflows.meeting_minutes import save_minutes
from app.library.workflows.meeting_report import meeting_pdf, report_data
from app.library.workflows.meeting_schedule import meeting_schedule
from app.library.workflows.meeting_store import Viewer
from app.systems.group import MEETING_STORE as GROUP_STORE
from app.systems.group.app import app
from app.systems.k_electronics import MEETING_STORE as STORE

pytestmark = pytest.mark.usefixtures("meeting_db")
needs_fonts = pytest.mark.skipif(not meeting_report.fonts_available(), reason="PDF 글꼴(나눔) 없음 — agent-backend 이미지에서 실행")

TODAY = date(2026, 10, 25)
SECRETARY = Viewer("secretary@example.com", "이서연", frozenset({"secretary"}), 3, True)
STAFF = Viewer("staff@example.com", "박지훈", frozenset({"staff"}), 2, True)

MINUTES = dict(
    title="인수 검토회의",
    date="2026-10-26",
    summary="실사 착수와 가격 상한을 정했다.",
    classification=3,
    attendees=[{"name": "김민수", "title": "CEO", "role": "chair"}, {"name": "이서연", "title": "CTO", "role": "secretary"},
               {"name": "박지훈", "title": "CFO", "attended": False}],
    decisions=[
        {"kind": "decision", "speaker": "김민수 (CEO)", "statement": "실사에 착수한다"},
        {"kind": "resolution", "statement": "가격 상한 1.5조 원", "outcome": "approved", "votes_for": 2, "votes_against": 0},
        {"kind": "issue", "statement": "핵심 인력 유지 방안"},
    ],
    action_items=[{"text": "실사 계획서 제출", "owner": "이서연", "due": "2026-11-04"}],
)


def held_meeting(store=STORE) -> str:
    """A scheduled meeting with an agenda, its final minutes, and a follow-up that carries its open issue."""
    meeting_schedule(store=store, viewer=SECRETARY, action="create", title="인수 검토회의", starts_at="2026-10-26 14:00",
                     ends_at="2026-10-26 15:00", location="본사 대회의실", agenda=["가격 범위", "실사 착수"], classification=3, today=TODAY)
    code = "MTG-2026-10-26-01"
    assert save_minutes(store=store, viewer=SECRETARY, meeting_id=code, **MINUTES)["saved"]
    assert save_minutes(store=store, viewer=SECRETARY, meeting_id=code, finalize=True, **MINUTES)["finalized"]
    meeting_schedule(store=store, viewer=SECRETARY, action="create", title="인수 2차 검토회의", starts_at="2026-11-02 10:00",
                     classification=3, carry_over_from=code, today=TODAY)
    return code


def test_report_data_has_what_the_documents_show():
    code = held_meeting()
    with STORE.connect() as conn:
        mid = conn.execute("SELECT id FROM meetings WHERE code = %s", (code,)).fetchone()["id"]
        data = report_data(conn, mid)
    assert data["status"] == "final" and data["agenda"] == ["가격 범위", "실사 착수"] and data["location"] == "본사 대회의실"
    assert meeting_report._when(data["starts_at"], data["ends_at"]) == "2026. 10. 26.(월) 14:00 ~ 15:00"
    assert [a["name"] for a in data["attendees"] if a["attended"] is False] == ["박지훈 (CFO)"]
    assert data["next_meeting"]["code"] == "MTG-2026-11-02-01" and data["next_meeting"]["carried"] == 1
    assert meeting_report.filename(data, "minutes") == "2026-10-26_인수 검토회의_회의록.pdf"
    text, subs = meeting_report._decision_line(data["decisions"][1])
    assert text == "가격 상한 1.5조 원" and subs == ["의결 결과: 원안가결 (찬성 2 · 반대 0)"]


@needs_fonts
def test_both_documents_render_for_those_who_may_read_the_meeting():
    code = held_meeting()
    for kind in ("minutes", "report"):
        out = meeting_pdf(store=STORE, viewer=SECRETARY, meeting_id=code, kind=kind)
        assert out["content"].startswith(b"%PDF-") and out["filename"].endswith(f"_{meeting_report.KINDS[kind]}.pdf")
    assert "error" in meeting_pdf(store=STORE, viewer=STAFF, meeting_id=code, kind="minutes")  # 대외비, clearance 2
    assert "error" in meeting_pdf(store=STORE, viewer=SECRETARY, meeting_id="MTG-2026-11-02-01", kind="minutes")  # no minutes yet
    assert "error" in meeting_pdf(store=STORE, viewer=SECRETARY, meeting_id=code, kind="slides")


@needs_fonts
def test_desk_download_is_a_pdf_with_the_korean_file_name():
    code = held_meeting(GROUP_STORE)
    client = TestClient(app)
    principal = {"email": "secretary@example.com", "name": "이서연", "groups": ["secretary"], "clearance": 3}
    res = client.post("/api/desk/report", json={"principal": principal, "meeting_id": code, "kind": "report"})
    assert res.status_code == 200 and res.headers["content-type"] == "application/pdf" and res.content.startswith(b"%PDF-")
    assert "filename*=UTF-8''2026-10-26_" in res.headers["content-disposition"]
    staff = {**principal, "email": "staff@example.com", "groups": ["staff"], "clearance": 2}
    assert "error" in client.post("/api/desk/report", json={"principal": staff, "meeting_id": code, "kind": "report"}).json()
