"""Signed principals, schema migrations, and the meeting workflows added on top of the minutes:
finalize permission, Compliance review, transcripts from the speech service, the mail outbox
(addresses the agent finds itself, delivery without approval, reminders) and task requests another
system sends over A2A."""

import json
import smtplib
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from fastapi import HTTPException

from app.library.workflows import compliance, meeting_minutes, meeting_report, outbox
from app.library.workflows.action_items import track_action_items, update_action_item
from app.library.workflows.jobs import run_pass, start_meeting_jobs
from app.library.workflows.meeting_minutes import save_minutes
from app.library.workflows.meeting_schedule import meeting_schedule
from app.library.workflows.meeting_store import MeetingStore, SCHEMA_FILE, Viewer, migrations, seed_directory
from app.library.workflows.outbox import deliver_approved, manage_outbox, queue_reminders
from app.library.workflows.task_requests import task_requests
from app.library.workflows.transcripts import TranscriptUnavailable, fetch_transcript, parse_clock, purge_expired
from app.runtime import principal as principal_mod
from app.runtime.a2a import request_review
from app.runtime.api import run_identity
from app.runtime.events import new_run, stream_run
from app.runtime.schemas import Principal, RunRequest
from app.systems.k_electronics import MEETING_STORE as STORE
from app.systems.k_electronics import registry as k_registry

TODAY = date(2026, 10, 25)
SECRETARY = Viewer("secretary@example.com", "이서연", frozenset({"secretary"}), 3, True)
STAFF = Viewer("staff@example.com", "박지훈", frozenset({"staff"}), 3, True)
GROUP_VIA_A2A = Viewer("ceo@example.com", "김민수", frozenset({"executive"}), 4, False)
CEO = Principal(email="ceo@example.com", name="김민수", groups=("executive",), clearance=4)

MINUTES = dict(
    title="공급망 점검회의",
    date="2026-10-24",
    summary="2차 협력사 단가 조정안을 검토하고 재고 기준을 정했다.",
    classification=2,
    attendees=[{"name": "김민수", "title": "CEO", "role": "chair"}, {"name": "박지훈", "title": "CFO"},
               {"name": "이서연", "role": "secretary"}],
    decisions=[{"kind": "decision", "statement": "안전재고를 4주분으로 늘린다"}],
    action_items=[{"text": "협력사별 재고 현황 보고", "owner": "박지훈", "due": "2026-10-28"}],
)

TRANSCRIPT = {
    "id": "TR-20261024-ABC123", "source_name": "공급망회의.wav", "duration": 95.0, "engine": "local",
    "turns": [
        {"start": 3.2, "end": 20.0, "speaker": "김민수 (CEO)", "score": 0.81, "text": "안전재고를 4주분으로 늘리겠습니다."},
        {"start": 75.4, "end": 90.0, "speaker": "박지훈 (CFO)", "score": 0.77,
         "text": "경쟁사와 단가를 맞추자는 얘기는 회의록에 남기지 맙시다."},
        {"start": 91.0, "end": 95.0, "speaker": "미확인 화자 1", "score": 0.31, "text": "재고 현황은 제가 정리하겠습니다."},
    ],
}


def save(viewer=SECRETARY, **overrides):
    return save_minutes(store=STORE, viewer=viewer, **{**MINUTES, **overrides})


@pytest.fixture
def directory(monkeypatch):
    """Registered emails by name (what the gateway's directory lookup would answer)."""
    emails = {"김민수": "ceo@example.com", "박지훈": "staff@example.com"}
    monkeypatch.setattr(outbox, "lookup_email", lambda name: (emails[name], f"sub-{name}") if name in emails else None)
    return emails


# ---------------------------------------------------------------------------
# Signed principal
# ---------------------------------------------------------------------------


def test_principal_token_round_trip_and_rejections():
    token = principal_mod.sign(CEO, "k" * 32, now=1_000_000)
    assert principal_mod.verify(token, "k" * 32, now=1_000_100) == CEO
    with pytest.raises(principal_mod.PrincipalRejected, match="서명"):
        principal_mod.verify(token, "x" * 32, now=1_000_100)
    with pytest.raises(principal_mod.PrincipalRejected, match="만료"):
        principal_mod.verify(token, "k" * 32, now=1_000_000 + 1800 + 31)
    header, payload, sig = token.split(".")
    forged = payload[:-2] + ("AA" if payload[-2:] != "AA" else "BB")
    with pytest.raises(principal_mod.PrincipalRejected):
        principal_mod.verify(f"{header}.{forged}.{sig}", "k" * 32, now=1_000_100)
    with pytest.raises(principal_mod.PrincipalRejected, match="형식"):
        principal_mod.verify("not-a-token", "k" * 32)
    assert CEO.agents is None  # no claim → not restricted
    limited = CEO.model_copy(update={"agents": ("meeting", "news")})
    assert principal_mod.verify(principal_mod.sign(limited, "k" * 32), "k" * 32).agents == ("meeting", "news")


def test_runs_take_the_principal_only_from_a_valid_token_when_a_key_is_set(monkeypatch):
    bare = RunRequest(request="안녕", principal=CEO)
    monkeypatch.delenv("PRINCIPAL_SIGNING_KEY", raising=False)
    assert run_identity(bare) == {"principal": CEO, "principal_token": None}  # local demo / tests: as sent

    monkeypatch.setenv("PRINCIPAL_SIGNING_KEY", "k" * 32)
    with pytest.raises(HTTPException) as e:
        run_identity(bare)
    assert e.value.status_code == 401
    assert run_identity(RunRequest(request="안녕")) == {"principal": None, "principal_token": None}
    token = principal_mod.sign(CEO, "k" * 32)
    lowered = CEO.model_copy(update={"clearance": 1})  # the body says one thing, the signature another
    assert run_identity(RunRequest(request="안녕", principal=lowered, principal_token=token))["principal"] == CEO
    with pytest.raises(HTTPException):
        run_identity(RunRequest(request="안녕", principal_token=principal_mod.sign(CEO, "x" * 32)))


def test_a2a_forwards_the_signed_token():
    seen = {}

    def peer(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        lines = [{"type": "agent_end", "agent": "k_super", "response": "접수했습니다"}, {"type": "done"}]
        return httpx.Response(200, text="\n".join(json.dumps(x) for x in lines))

    with httpx.Client(transport=httpx.MockTransport(peer)) as client:
        answer = request_review(client, "http://k/api/a2a/run", "업무 요청: 재고 점검", caller="group", trace_id="t",
                                principal=CEO, principal_token="signed")
    assert answer == "접수했습니다" and seen["principal_token"] == "signed" and seen["principal"]["email"] == CEO.email


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------


def test_schema_records_its_migrations(meeting_db):
    with STORE.connect() as conn:
        versions = [r["version"] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions == [1] + [v for v, _, _ in migrations()] and 2 in versions


def test_a_deployment_may_start_without_the_sample_meetings(meeting_db, monkeypatch):
    from app.systems.group import MEETING_STORE as GROUP_STORE  # the Group fixture has a staff directory

    monkeypatch.setenv("MEETINGS_SEED_MEETINGS", "0")
    with GROUP_STORE.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM meetings").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM staff_directory").fetchone()["n"] > 0  # addresses still known


def test_a_schema_created_before_the_migrations_is_upgraded_in_place(meeting_db):
    import psycopg
    from psycopg import sql

    legacy = MeetingStore("legacy_test")
    with psycopg.connect(meeting_db, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public")
        conn.execute("DROP SCHEMA IF EXISTS legacy_test CASCADE")
        conn.execute("CREATE SCHEMA legacy_test")
        conn.execute("SET search_path TO legacy_test, public")
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))  # what deployments have from before 002
        conn.execute("INSERT INTO meetings (code, title, starts_at) VALUES ('MTG-OLD', '기존 회의', now())")
    try:
        with legacy.connect() as conn:
            assert conn.execute("SELECT count(*) AS n FROM meetings").fetchone()["n"] == 1  # data kept, no re-seed
            assert conn.execute("SELECT to_regclass('legacy_test.outbox') AS t").fetchone()["t"] is not None
        legacy.reset()
        with legacy.connect() as conn:  # second start: nothing applied twice
            assert conn.execute("SELECT count(*) AS n FROM schema_migrations").fetchone()["n"] == 1 + len(migrations())
    finally:
        with psycopg.connect(meeting_db, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier("legacy_test")))


# ---------------------------------------------------------------------------
# Finalize permission
# ---------------------------------------------------------------------------


def test_only_confirmers_finalize_or_correct(meeting_db, directory):
    draft = save(viewer=STAFF)
    assert draft["saved"] and draft["status"] == "draft"
    out = save(viewer=STAFF, meeting_id=draft["meeting_id"], finalize=True)
    assert out["status"] == "draft" and any("간사" in w for w in out["warnings"])
    final = save(viewer=SECRETARY, meeting_id=draft["meeting_id"], finalize=True)
    assert final["finalized"], final["warnings"]
    assert "간사" in save(viewer=STAFF, meeting_id=draft["meeting_id"], correction_reason="오타")["error"]


def test_the_meetings_own_chair_may_finalize(meeting_db, directory, monkeypatch):
    monkeypatch.setenv("MEETINGS_CONFIRM_GROUPS", "secretary")
    exec_viewer = Viewer("ceo@example.com", "김민수", frozenset({"executive"}), 3, True)
    with STORE.connect() as conn:  # 김민수 (CEO) in the directory with the email he registered
        conn.execute("INSERT INTO people (display_name, title, email) VALUES ('김민수', 'CEO', 'ceo@example.com')")
    # The call that names its requester chair does not count that role yet: participants as they were decide.
    first = save(viewer=exec_viewer, finalize=True)
    assert first["status"] == "draft" and any("간사" in w for w in first["warnings"])
    assert save(viewer=exec_viewer, meeting_id=first["meeting_id"], finalize=True)["finalized"]
    # Someone else on the same meeting, not chair or secretary, still cannot.
    assert "간사" in save(viewer=STAFF, meeting_id=first["meeting_id"], correction_reason="x")["error"]


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,category", [
    ("실적 발표 전에 주식을 좀 사 두는 게 좋겠습니다", "insider_trading"),
    ("경쟁사와 단가를 맞추는 쪽으로 가죠", "collusion"),
    ("입찰 가격은 미리 합의해 두었습니다", "collusion"),
    ("심사위원에게 골프 접대를 준비하세요", "bribery"),
    ("이번 분기 손실은 다음 분기로 숨기고 보고합시다", "accounting_fraud"),
    ("그 메일은 전부 삭제하세요", "record_destruction"),
    ("이 얘기는 회의록에 남기지 마세요", "record_destruction"),
    ("노조 가입한 사람은 승진에서 불이익을 줍시다", "unfair_labor"),
    ("담당자 연락처는 010-1234-5678입니다", "personal_data"),
    ("주민번호 앞자리 900101-1234567 확인", "personal_data"),
])
def test_rules_flag_risky_statements(text, category):
    assert category in {rule.category for rule, _ in compliance.scan(text)}


@pytest.mark.parametrize("text", [
    "가격 상한을 2.3조 원으로 낮추고 고객 집중도 실사를 확대한다",
    "안전재고를 4주분으로 늘린다",
    "분식집 앞에서 만나기로 했다",
    "2026-10-28까지 협력사별 재고 현황을 보고한다",
    "공시 일정은 IR팀이 확인한다",
])
def test_rules_leave_ordinary_statements_alone(text):
    assert compliance.scan(text) == []


def test_open_flags_hold_finalization_until_reviewed(meeting_db, directory):
    out = save(body="박지훈: 경쟁사와 단가를 맞추자는 얘기가 있었으나 결론 없음")
    flag = out["compliance_flags"][0]
    assert flag["status"] == "open" and flag["location"] == "본문" and "공정거래법" in flag["rule"]
    held = save(meeting_id=out["meeting_id"], finalize=True)
    assert held["status"] == "draft" and any("Compliance" in w for w in held["warnings"])
    # Reviews from someone without confirm permission are not applied.
    ignored = save(viewer=STAFF, meeting_id=out["meeting_id"], flag_reviews=[{"id": flag["id"], "status": "cleared"}])
    assert ignored["compliance_flags"][0]["status"] == "open"
    # acknowledged needs a note.
    noted = save(meeting_id=out["meeting_id"], flag_reviews=[{"id": flag["id"], "status": "acknowledged"}])
    assert noted["compliance_flags"][0]["status"] == "open" and any("note" in w for w in noted["warnings"])
    done = save(meeting_id=out["meeting_id"], finalize=True,
                flag_reviews=[{"id": flag["id"], "status": "acknowledged", "note": "법무팀 검토 의뢰"}])
    assert done["finalized"] and done["compliance_flags"][0]["review_note"] == "법무팀 검토 의뢰"


def test_rewriting_the_passage_drops_its_open_flag(meeting_db):
    out = save(summary="자료는 모두 파기하기로 했다")
    assert [f["location"] for f in out["compliance_flags"]] == ["요약"]
    assert save(meeting_id=out["meeting_id"])["compliance_flags"] == []


# ---------------------------------------------------------------------------
# Transcripts
# ---------------------------------------------------------------------------


def test_transcript_is_attached_by_id_and_cited_by_time(meeting_db, monkeypatch):
    monkeypatch.setattr(meeting_minutes, "fetch_transcript", lambda ref: TRANSCRIPT)
    out = save(transcript_id="TR-20261024-ABC123",
               decisions=[{"kind": "decision", "statement": "안전재고를 4주분으로 늘린다", "source_time": "00:03"}],
               action_items=[{"text": "협력사별 재고 현황 보고", "owner": "박지훈", "due": "2026-10-28", "source_time": "01:31"},
                             {"text": "단가 자료 정리", "owner": "박지훈", "due": "2026-10-30", "source_time": "09:59"}])
    assert out["transcripts"] == ["TR-20261024-ABC123"]
    assert any("09:59" in w for w in out["warnings"])  # no statement at that time: not linked, said so
    # The flagged statement in the transcript is held for review like the minutes text.
    assert any(f["location"].startswith("transcript [01:15] 박지훈 (CFO)") for f in out["compliance_flags"])
    with STORE.connect() as conn:
        segs = conn.execute("SELECT seq, speaker_label, person_id, speaker_score FROM transcript_segments ORDER BY seq").fetchall()
        assert [s["speaker_label"] for s in segs] == ["김민수 (CEO)", "박지훈 (CFO)", "미확인 화자 1"]
        assert segs[0]["person_id"] is not None and segs[2]["person_id"] is None
        assert conn.execute("SELECT source_seq FROM decisions WHERE statement LIKE '안전재고%%'").fetchone()["source_seq"] == 1
        items = conn.execute("SELECT text, source_seq FROM action_items WHERE meeting_id = (SELECT id FROM meetings WHERE code = %s) ORDER BY id",
                             (out["meeting_id"],)).fetchall()
        assert [i["source_seq"] for i in items] == [3, None]
    # Attaching it again is a no-op; to another meeting it is refused.
    assert save(meeting_id=out["meeting_id"], transcript_id="TR-20261024-ABC123")["transcripts"] == ["TR-20261024-ABC123"]
    other = save(title="다른 회의", transcript_id="TR-20261024-ABC123")
    assert other["transcripts"] == [] and any("다른 회의" in w for w in other["warnings"])

    # Past its retention date the transcript goes; the decision stays, unlinked.
    with STORE.connect() as conn:
        conn.execute("UPDATE transcripts SET expires_at = now() - interval '1 day'")
        assert purge_expired(conn) == 1
        row = conn.execute("SELECT statement, source_transcript_id FROM decisions WHERE statement LIKE '안전재고%%'").fetchone()
        assert row["source_transcript_id"] is None and conn.execute("SELECT count(*) AS n FROM transcript_segments").fetchone()["n"] == 0


def test_time_stamps_without_an_attached_transcript_are_ignored(meeting_db):
    out = save(decisions=[{"kind": "decision", "statement": "안전재고를 4주분으로 늘린다", "source_time": "00:03"}])
    assert out["saved"] and not any("근거 발언" in w for w in out["warnings"])


def test_transcript_unavailable_is_a_warning_not_a_failure(meeting_db, monkeypatch):
    monkeypatch.delenv("SPEECH_SERVICE_URL", raising=False)
    out = save(transcript_id="TR-20261024-ABC123")
    assert out["saved"] and any("SPEECH_SERVICE_URL" in w for w in out["warnings"])


def test_fetch_transcript_over_http(monkeypatch):
    monkeypatch.setenv("SPEECH_SERVICE_URL", "http://speech:8000")
    monkeypatch.setenv("SPEECH_API_KEY", "secret")

    def speech(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret"
        if request.url.path.endswith("TR-20261024-ABC123"):
            return httpx.Response(200, json=TRANSCRIPT)
        return httpx.Response(404, json={"error": "not found"})

    with httpx.Client(transport=httpx.MockTransport(speech)) as client:
        assert fetch_transcript("tr-20261024-abc123", client=client)["turns"][0]["speaker"] == "김민수 (CEO)"
        with pytest.raises(TranscriptUnavailable, match="삭제"):
            fetch_transcript("TR-20261024-000000", client=client)
    with pytest.raises(TranscriptUnavailable, match="형식"):
        fetch_transcript("../etc/passwd")
    assert parse_clock("[01:02:03]") == 3723 and parse_clock("03:15") == 195 and parse_clock("오후") is None


# ---------------------------------------------------------------------------
# Mail outbox
# ---------------------------------------------------------------------------


class FakeSMTP:
    def __init__(self, fail_for: str | None = None):
        self.sent, self.fail_for = [], fail_for

    def send_message(self, msg):
        if self.fail_for and self.fail_for in msg["To"]:
            raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no such user")})
        self.sent.append(msg)

    def quit(self):
        pass


def test_finalizing_sends_mail_without_anyone_approving_it(meeting_db, directory, monkeypatch):
    draft = save()
    final = save(meeting_id=draft["meeting_id"], finalize=True)
    queued = {(m["kind"], m["to"]): m for m in final["mail_queued"]}
    assert queued[("minutes", "김민수 (CEO)")]["status"] == "approved" and queued[("minutes", "김민수 (CEO)")]["email"] == "ceo@example.com"
    assert queued[("minutes", "이서연")]["status"] == "needs_address"  # registered nowhere and not in the staff directory
    assert queued[("assignment", "박지훈 (CFO)")]["subject"] == "[업무 배정] 협력사별 재고 현황 보고 (기한 2026-10-28)"

    # The next delivery pass sends what has an address.
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    smtp = FakeSMTP()
    with STORE.connect() as conn:
        assert deliver_approved(conn, connect=lambda cfg: smtp) == 3
    assert all("자동으로 보냈습니다" in m.get_body(("plain",)).get_content() for m in smtp.sent)

    listed = manage_outbox(store=STORE, viewer=STAFF, meeting_id=draft["meeting_id"])  # sent mail stays listed for a week
    assert sorted(m["status"] for m in listed["mails"]) == ["needs_address", "sent", "sent", "sent"]
    assert all(m["sent_at"] for m in listed["mails"] if m["status"] == "sent")
    assert "협력사별 재고 현황 보고" in [m for m in listed["mails"] if m["kind"] == "assignment"][0]["body"]
    assert "간사" in manage_outbox(store=STORE, viewer=STAFF, action="resend", meeting_id=draft["meeting_id"])["error"]

    secretary_mail = queued[("minutes", "이서연")]["id"]
    bad = manage_outbox(store=STORE, viewer=SECRETARY, action="set_address", mail_ids=[secretary_mail], email="not-an-email")
    assert bad["ok"] is False
    fixed = manage_outbox(store=STORE, viewer=SECRETARY, action="set_address", mail_ids=[secretary_mail], email="secretary@example.com")
    assert fixed["mails"][0]["status"] == "approved"  # a corrected address goes straight out
    with STORE.connect() as conn:
        assert deliver_approved(conn, connect=lambda cfg: smtp) == 1
    assert "이서연 <secretary@example.com>" in [m["To"] for m in smtp.sent]
    # A correction re-sends the minutes, but not the unchanged assignment.
    corrected = save(meeting_id=draft["meeting_id"], summary="재고 기준을 정했다.", correction_reason="요약 정정")
    assert {m["kind"] for m in corrected["mail_queued"]} == {"minutes"} and "[회의록 정정]" in corrected["mail_queued"][0]["subject"]


@pytest.mark.skipif(not meeting_report.fonts_available(), reason="PDF 글꼴(나눔) 없음 — agent-backend 이미지에서 실행")
def test_minutes_mail_carries_the_minutes_pdf_and_the_chair_also_the_report(meeting_db, directory, monkeypatch):
    draft = save()
    save(meeting_id=draft["meeting_id"], finalize=True)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    smtp = FakeSMTP()
    with STORE.connect() as conn:
        deliver_approved(conn, connect=lambda cfg: smtp)
    attached = {m["To"].addresses[0].addr_spec: sorted(a.get_filename().rsplit("_", 1)[-1] for a in m.iter_attachments())
                for m in smtp.sent if m["Subject"].startswith("[회의록]")}
    assert attached.pop("ceo@example.com") == ["회의 결과 보고서.pdf", "회의록.pdf"]  # 김민수, the chair
    assert attached and all(files == ["회의록.pdf"] for files in attached.values())
    assert all(not a for m in smtp.sent if m["Subject"].startswith("[업무 배정]") for a in [list(m.iter_attachments())])


def test_mail_without_an_address_can_be_cancelled_or_replaced(meeting_db, directory):
    draft = save(action_items=[{"text": "현황 보고", "owner": "이서연", "due": "2026-10-28"}])  # nobody registered as 이서연
    final = save(meeting_id=draft["meeting_id"], finalize=True, action_items=[{"text": "현황 보고", "owner": "이서연", "due": "2026-10-28"}])
    waiting = [m["id"] for m in final["mail_queued"] if m["to"] == "이서연" and m["kind"] == "minutes"]
    out = manage_outbox(store=STORE, viewer=SECRETARY, action="reject", mail_ids=waiting, note="불참자")
    assert out["ok"] and out["done"] == waiting
    with STORE.connect() as conn:  # a newer reminder replaces one still waiting for an address
        assert queue_reminders(conn, date(2026, 10, 25)) == 1 and queue_reminders(conn, date(2026, 10, 27)) == 1
        rows = conn.execute("SELECT status FROM outbox WHERE kind = 'reminder' ORDER BY id").fetchall()
    assert [r["status"] for r in rows] == ["rejected", "needs_address"]


def test_delivery_retries_then_gives_up(meeting_db, directory, monkeypatch):
    only_ceo = dict(attendees=[{"name": "김민수", "role": "chair"}], action_items=[{"text": "보고", "owner": "재무팀", "due": "2026-10-30"}])
    draft = save(**only_ceo)
    final = save(meeting_id=draft["meeting_id"], finalize=True, **only_ceo)
    assert any("부서(재무팀)" in w for w in final["warnings"])  # a unit owner gets no assignment mail, and it is said
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    for _ in range(outbox.MAX_ATTEMPTS):
        with STORE.connect() as conn:
            deliver_approved(conn, connect=lambda cfg: FakeSMTP(fail_for="ceo@example.com"))
    failed = manage_outbox(store=STORE, viewer=SECRETARY, meeting_id=draft["meeting_id"])["mails"]
    assert [m["status"] for m in failed] == ["failed"] and "no such user" in failed[0]["last_error"]
    assert manage_outbox(store=STORE, viewer=SECRETARY, action="resend", meeting_id=draft["meeting_id"])["mails"][0]["status"] == "approved"


def test_mail_the_server_could_not_send_for_days_is_not_sent_late(meeting_db, directory, monkeypatch):
    only_ceo = dict(attendees=[{"name": "김민수", "role": "chair"}], action_items=[{"text": "보고", "owner": "재무팀", "due": "2026-10-30"}])
    draft = save(**only_ceo)
    save(meeting_id=draft["meeting_id"], finalize=True, **only_ceo)
    with STORE.connect() as conn:  # no mail server for four days
        conn.execute("UPDATE outbox SET approved_at = now() - interval '4 days'")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    with STORE.connect() as conn:
        assert deliver_approved(conn, connect=lambda cfg: FakeSMTP()) == 0
    mails = manage_outbox(store=STORE, viewer=SECRETARY, meeting_id=draft["meeting_id"])["mails"]
    assert [m["status"] for m in mails] == ["failed"] and "3일" in mails[0]["last_error"]


def test_the_agent_finds_addresses_itself_and_never_guesses(meeting_db, directory):
    with STORE.connect() as conn:
        seed_directory(conn, [
            {"name": "이서연", "title": "CTO", "email": "seoyeon.lee@kelec.example.com"},
            {"name": "최민준", "title": "CFO", "organization": "K전자", "email": "minjun.choi@kelec.example.com"},
            {"name": "한상우", "title": "자문위원", "organization": "OO회계법인", "email": "advisor@oo-cpa.example.com", "external": True},
        ])
    people = [{"name": "김민수", "title": "CEO", "role": "chair"}, {"name": "이서연", "role": "secretary"},
              {"name": "외부 자문위원", "title": "OO회계법인"}, {"name": "최민준", "title": "CTO"}, {"name": "한상우", "title": "OO회계법인 자문위원"},
              {"name": "미확인 화자 1"}]
    draft = save(attendees=people)
    final = save(meeting_id=draft["meeting_id"], finalize=True, attendees=people)
    minutes = {m["to"]: (m["email"], m["status"]) for m in final["mail_queued"] if m["kind"] == "minutes"}
    assert minutes == {
        "김민수 (CEO)": ("ceo@example.com", "approved"),  # the email registered at login
        "이서연": ("seoyeon.lee@kelec.example.com", "approved"),  # the staff directory, by name
        "외부 자문위원 (OO회계법인)": ("advisor@oo-cpa.example.com", "approved"),  # that firm's one advisor
        "최민준 (CTO)": (None, "needs_address"),  # the directory's 최민준 is the CFO: not taken for this one
        "한상우 (OO회계법인 자문위원)": ("advisor@oo-cpa.example.com", "approved"),  # a title that contains the directory's
    }  # and nothing for the unidentified speaker


def test_unnamed_voices_stay_labels_and_need_a_real_owner(meeting_db, directory):
    people = [{"name": "김민수", "title": "CEO", "role": "chair"}, {"name": "이서연", "role": "secretary"},
              {"name": "화자 A"}, {"name": "화자 B"}]
    said = [{"kind": "opinion", "speaker": "화자 B", "statement": "재고를 더 늘려야 합니다"}]
    task = {"text": "협력사별 재고 현황 보고", "owner": "화자 A", "due": "2026-10-28"}
    draft = save(attendees=people, decisions=said, action_items=[task], finalize=True)
    assert not draft["finalized"] and any("'화자 A'의 실제 담당자 지정 필요" in w for w in draft["warnings"])
    final = save(meeting_id=draft["meeting_id"], finalize=True, attendees=people, decisions=said,
                 action_items=[{**task, "owner": "박지훈"}])
    assert final["finalized"]
    assert not [m for m in final["mail_queued"] if m["to"].startswith("화자")]  # a voice has no mailbox
    other = save(title="다른 회의", attendees=[{"name": "화자 A"}], decisions=said, action_items=[])
    with STORE.connect() as conn:
        rows = conn.execute("SELECT speaker_id, speaker_label FROM decisions WHERE statement LIKE '재고를 더%%' ORDER BY id").fetchall()
        assert [(r["speaker_id"], r["speaker_label"]) for r in rows] == [(None, "화자 B"), (None, "화자 B")]  # never one person
    assert other["saved"]


def test_reminders_before_and_after_the_due_date(meeting_db, directory):
    draft = save()
    save(meeting_id=draft["meeting_id"], finalize=True)  # 박지훈's item is due 2026-10-28
    with STORE.connect() as conn:
        assert queue_reminders(conn, date(2026, 10, 24)) == 0
        assert queue_reminders(conn, date(2026, 10, 25)) == 1  # D-3
        assert queue_reminders(conn, date(2026, 10, 25)) == 0  # once a day
        assert queue_reminders(conn, date(2026, 10, 27)) == 1  # D-1 replaces the D-3 not sent yet
        assert queue_reminders(conn, date(2026, 10, 29)) == 1  # a day late
        assert queue_reminders(conn, date(2026, 10, 30)) == 0
        assert queue_reminders(conn, date(2026, 11, 5)) == 1   # a week later
        rows = conn.execute("SELECT subject, status FROM outbox WHERE kind = 'reminder' ORDER BY id").fetchall()
    assert [r["status"] for r in rows] == ["rejected", "rejected", "rejected", "approved"]
    assert rows[1]["subject"].startswith("[기한 알림] D-1") and rows[3]["subject"].startswith("[기한 경과] 8일 지남")


def test_a_reminder_asked_for_now(meeting_db, directory):
    draft = save()
    item = f"{draft['meeting_id']}-A1"
    nudge = dict(store=STORE, viewer=SECRETARY, item_id=item, remind=True, today=date(2026, 10, 30))
    early = update_action_item(**nudge)
    assert not early["updated"] and "초안" in early["error"]  # not assigned before the minutes are final
    save(meeting_id=draft["meeting_id"], finalize=True)
    out = update_action_item(**{**nudge, "note": "이번 주 안에 공유 부탁드립니다"})
    assert [(m["to"], m["subject"]) for m in out["mail_queued"]] == [("박지훈 (CFO)", "[업무 독촉] 2일 지남: 협력사별 재고 현황 보고 (기한 2026-10-28)")]
    again = update_action_item(**nudge)
    assert not again["updated"] and "오늘 이미" in again["error"]  # once a day
    with STORE.connect() as conn:
        body = conn.execute("SELECT body FROM outbox WHERE dedupe_key LIKE 'nudge:%%'").fetchone()["body"]
    assert "이번 주 안에 공유 부탁드립니다" in body and "요청한 사람: 이서연" in body
    update_action_item(store=STORE, viewer=SECRETARY, item_id=item, status="done", today=date(2026, 10, 31))
    assert "이미 'done'" in update_action_item(**{**nudge, "today": date(2026, 10, 31)})["error"]


def test_scheduling_queues_notices(meeting_db, directory):
    out = meeting_schedule(store=STORE, viewer=SECRETARY, action="create", title="공급망 후속회의", starts_at="2026-11-03 14:00",
                           attendees=["김민수", "박지훈"], agenda=["재고 보고"], today=TODAY)
    assert [(m["to"], m["status"]) for m in out["mail_queued"]] == [("김민수", "approved"), ("박지훈", "approved")]
    assert out["mail_queued"][0]["subject"] == "[회의 안내] 공급망 후속회의 2026-11-03 14:00"
    cancelled = meeting_schedule(store=STORE, viewer=SECRETARY, action="cancel", meeting_id=out["meeting"]["id"], today=TODAY)
    assert all(m["subject"].startswith("[회의 취소]") for m in cancelled["mail_queued"])
    with STORE.connect() as conn:  # the invitations not sent yet do not go out after the cancellation
        rows = conn.execute("SELECT subject, status FROM outbox WHERE kind = 'schedule' ORDER BY id").fetchall()
    assert [(r["subject"][:7], r["status"]) for r in rows] == [("[회의 안내]", "rejected")] * 2 + [("[회의 취소]", "approved")] * 2


def test_background_pass_queues_reminders_and_purges(meeting_db, directory, monkeypatch):
    assert start_meeting_jobs(STORE, stop=None) is None  # off unless MEETINGS_BACKGROUND_JOBS=1
    items = [{"text": "보고", "owner": "박지훈", "due": (datetime.now(timezone(timedelta(hours=9))).date() + timedelta(days=1)).isoformat()}]
    draft = save(action_items=items)
    save(meeting_id=draft["meeting_id"], action_items=items, finalize=True)  # not MINUTES' fixed-date items
    assert run_pass(STORE, daily=True)["reminders"] == 1
    assert run_pass(STORE, daily=False) == {"sent": 0, "reminders": 0, "addressed": 0, "purged": 0}


def test_waiting_mail_finds_an_address_registered_later(meeting_db, directory):
    draft = save()
    final = save(meeting_id=draft["meeting_id"], finalize=True)
    assert [m["status"] for m in final["mail_queued"] if m["to"] == "이서연"] == ["needs_address"]
    directory["이서연"] = "seoyeon@example.com"  # 이서연 registers
    assert run_pass(STORE, daily=True)["addressed"] == 1
    listed = manage_outbox(store=STORE, viewer=SECRETARY, meeting_id=draft["meeting_id"])["mails"]
    assert [(m["email"], m["status"]) for m in listed if m["to"] == "이서연"] == [("seoyeon@example.com", "approved")]


# ---------------------------------------------------------------------------
# Task requests over A2A
# ---------------------------------------------------------------------------


def submit(**overrides):
    args = dict(action="submit", title="K전자 HBM 라인 증설 투자안 재검토", detail="그룹 투자심의 지적사항 반영",
                due="2026-11-20", suggested_owner="전략기획팀", source_ref="MTG-2026-10-20-01-A2", classification=2)
    return task_requests(store=STORE, viewer=GROUP_VIA_A2A, caller="group", today=TODAY, **{**args, **overrides})


def test_a_request_from_another_system_waits_until_accepted(meeting_db, directory):
    sent = submit()
    assert sent["ok"] and sent["request"]["id"] == "REQ-2026-10-25-01" and sent["request"]["status"] == "pending"
    # Only A2A runs submit; A2A runs cannot accept.
    assert task_requests(store=STORE, viewer=SECRETARY, caller=None, action="submit", title="x")["ok"] is False
    assert task_requests(store=STORE, viewer=GROUP_VIA_A2A, caller="group", action="accept", request_id="REQ-2026-10-25-01")["ok"] is False
    # Not an Action Item yet.
    assert all("HBM" not in i["text"] for i in track_action_items(store=STORE, viewer=SECRETARY, today=TODAY)["items"])

    pending = task_requests(store=STORE, viewer=STAFF, caller=None, action="list", today=TODAY)
    assert [r["id"] for r in pending["requests"]] == ["REQ-2026-10-25-01"] and pending["requests"][0]["from"] == "그룹"
    assert "간사" in task_requests(store=STORE, viewer=STAFF, caller=None, action="accept", request_id="REQ-2026-10-25-01")["error"]
    accepted = task_requests(store=STORE, viewer=SECRETARY, caller=None, action="accept", request_id="REQ-2026-10-25-01",
                             owner="박지훈", today=TODAY)
    assert accepted["ok"], accepted
    item = accepted["request"]["action_item"]
    assert accepted["request"]["status"] == "accepted" and item["owner"] == "박지훈" and item["due"] == "2026-11-20"
    assert accepted["mail_queued"][0]["kind"] == "assignment" and accepted["mail_queued"][0]["status"] == "approved"
    tracked = track_action_items(store=STORE, viewer=SECRETARY, meeting_id=accepted["meeting_id"], today=TODAY)["items"]
    assert [i["id"] for i in tracked] == [item["id"]] and "REQ-2026-10-25-01" in tracked[0]["last_note"]

    # The requesting system sees how its own request stands.
    status = task_requests(store=STORE, viewer=GROUP_VIA_A2A, caller="group", action="list", today=TODAY)
    assert status["requests"][0]["action_item"]["id"] == item["id"]
    assert task_requests(store=STORE, viewer=GROUP_VIA_A2A, caller="other", action="list", today=TODAY)["matched"] == 0


def test_accept_needs_owner_and_due_and_reject_keeps_a_reason(meeting_db):
    submit(suggested_owner=None, due=None)
    missing = task_requests(store=STORE, viewer=SECRETARY, caller=None, action="accept", request_id="REQ-2026-10-25-01", today=TODAY)
    assert "담당(owner)과 기한(due)" in missing["error"]
    unit = task_requests(store=STORE, viewer=SECRETARY, caller=None, action="accept", request_id="REQ-2026-10-25-01",
                         owner="전략기획팀", due="2026-11-20", today=TODAY)
    assert unit["mail_queued"] == [] and "부서(전략기획팀)" in unit["notes"][0]
    submit(suggested_owner=None, due=None)
    rejected = task_requests(store=STORE, viewer=SECRETARY, caller=None, action="reject", request_id="REQ-2026-10-25-02",
                             note="이미 진행 중인 과제", today=TODAY)
    assert rejected["request"]["status"] == "rejected" and rejected["request"]["decision_note"] == "이미 진행 중인 과제"
    assert submit(classification=4, title="극비")["ok"] is True
    assert task_requests(store=STORE, viewer=Viewer("g@x", "g", frozenset(), 2, False), caller="group", action="submit",
                         title="t", classification=3)["ok"] is False  # above the forwarded user's clearance


def test_the_tool_takes_the_caller_from_the_run(meeting_db):
    tool = k_registry.get_capability("run_task_requests").impl
    out = []
    run = new_run(system="k_electronics", root_agent="meeting", origin="a2a", caller="group", principal=CEO)
    list(stream_run(lambda: out.append(tool.invoke({"action": "submit", "title": "재고 점검"})), run=run, hub=None))
    assert out[0]["ok"] and out[0]["request"]["from"] == "그룹"
    direct = []
    run = new_run(system="k_electronics", root_agent="meeting", principal=CEO)
    list(stream_run(lambda: direct.append(tool.invoke({"action": "submit", "title": "재고 점검"})), run=run, hub=None))
    assert direct[0]["ok"] is False


class DroppingSMTP(FakeSMTP):
    """Loses the connection while sending one subject."""

    def __init__(self, drop_subject: str):
        super().__init__()
        self.drop_subject = drop_subject

    def send_message(self, msg):
        if msg["Subject"].startswith(self.drop_subject):
            raise ConnectionResetError("connection reset by peer")
        super().send_message(msg)


def test_a_mail_that_cannot_go_out_does_not_undo_or_resend_the_others(meeting_db, directory, monkeypatch):
    draft = save()
    save(meeting_id=draft["meeting_id"], finalize=True)  # minutes → 김민수, 박지훈; assignment → 박지훈
    with STORE.connect() as conn:  # an address a header cannot take, stored before addresses were checked
        bad = conn.execute("SELECT min(id) AS id FROM outbox WHERE status = 'approved'").fetchone()["id"]
        conn.execute("UPDATE outbox SET to_email = 'ceo@example.com,x@example.com' WHERE id = %s", (bad,))
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    first = DroppingSMTP("[업무 배정]")
    with STORE.connect() as conn:  # the bad address fails while building the mail, the assignment while sending it
        assert deliver_approved(conn, connect=lambda cfg: first) == 1
    second = FakeSMTP()
    with STORE.connect() as conn:  # the mail already sent is recorded as sent and does not go out again
        assert deliver_approved(conn, connect=lambda cfg: second) == 1
    assert [m["Subject"].split("]")[0] for m in first.sent] == ["[회의록"]
    assert [m["Subject"].split("]")[0] for m in second.sent] == ["[업무 배정"]
    with STORE.connect() as conn:
        rows = {r["id"]: r for r in conn.execute("SELECT id, status, attempts, last_error FROM outbox WHERE status <> 'needs_address'")}
    assert rows[bad]["status"] == "approved" and rows[bad]["attempts"] == 2 and "ValueError" in rows[bad]["last_error"]
    assert sorted(r["status"] for i, r in rows.items() if i != bad) == ["sent", "sent"]


def test_addresses_are_checked_before_they_are_stored(meeting_db, directory):
    draft = save()
    final = save(meeting_id=draft["meeting_id"], finalize=True)
    waiting = [m["id"] for m in final["mail_queued"] if m["to"] == "이서연"]
    for wrong in ("a,b@example.com", "이서연 <s@example.com>", "s@example.com\nBcc: x@example.com"):
        out = manage_outbox(store=STORE, viewer=SECRETARY, action="set_address", mail_ids=waiting, email=wrong)
        assert out["ok"] is False, wrong
    assert outbox.valid_email("secretary@example.com") and not outbox.valid_email("a;b@example.com")


def test_another_system_may_only_ask_for_a_reminder(meeting_db, directory, monkeypatch):
    monkeypatch.setenv("MEETINGS_CONFIRM_GROUPS", "secretary,executive")
    draft = save()
    save(meeting_id=draft["meeting_id"], finalize=True)
    item = f"{draft['meeting_id']}-A1"
    a2a = dict(store=STORE, item_id=item, today=date(2026, 10, 30))
    for change in ({"status": "done"}, {"due": "2026-11-30"}, {"owner": "김민수"}, {"note": "진행 중"},
                   {"status": "done", "remind": True}):  # over A2A no field of the item may change
        out = update_action_item(viewer=GROUP_VIA_A2A, **a2a, **change)
        assert out["updated"] is False and "독촉 메일을 보내는 것만" in out["error"], change
    nobody = Viewer(None, "", frozenset(), 4, False)  # forwarded without a user who may confirm
    assert "의장 · 간사" in update_action_item(viewer=nobody, remind=True, **a2a)["error"]

    out = update_action_item(viewer=GROUP_VIA_A2A, remind=True, note="지연 사유를 알려 주세요", **a2a)
    assert out["updated"] and [(m["to"], m["status"]) for m in out["mail_queued"]] == [("박지훈 (CFO)", "approved")]
    assert out["item"]["status"] == "open" and out["item"]["due"] == "2026-10-28"  # the item itself is untouched
    assert out["history"][0]["note"] == "독촉 메일 (다른 Agent System 요청); 지연 사유를 알려 주세요"
    again = update_action_item(viewer=GROUP_VIA_A2A, remind=True, note="다시", **a2a)
    assert again["updated"] is False and "오늘 이미" in again["error"]  # once a day, and nothing recorded
    with STORE.connect() as conn:
        body = conn.execute("SELECT body FROM outbox WHERE dedupe_key LIKE 'nudge:%%'").fetchone()["body"]
        notes = conn.execute("SELECT count(*) AS n FROM action_item_updates u JOIN action_items a ON a.id = u.action_item_id "
                             "WHERE a.code = %s", (item,)).fetchone()["n"]
    assert "요청한 사람: 김민수 (다른 Agent System을 통한 요청)" in body and "None" not in body and notes == 1

    monkeypatch.setenv("MEETINGS_CONFIRM_GROUPS", "*")  # local demo: a forwarded request without a user may remind
    out = update_action_item(viewer=nobody, remind=True, **{**a2a, "today": date(2026, 10, 31)})
    assert out["updated"]
    with STORE.connect() as conn:
        last = conn.execute("SELECT body FROM outbox WHERE dedupe_key LIKE 'nudge:%%' ORDER BY id DESC LIMIT 1").fetchone()["body"]
    assert "요청한 사람: 다른 Agent System(그룹 등)의 요청" in last
