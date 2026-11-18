"""Progress reported from the mail: the buttons in assignment · reminder mail (library/workflows/mail_actions)."""

from __future__ import annotations

import re

import pytest

from app.library.workflows import mail_actions, outbox
from app.library.workflows.action_items import update_action_item
from app.library.workflows.meeting_control import control
from app.library.workflows.meeting_minutes import save_minutes
from app.library.workflows.meeting_store import Viewer
from app.library.workflows.outbox import deliver_approved
from app.systems.k_electronics import MEETING_STORE as STORE

SECRETARY = Viewer("secretary@example.com", "이서연", frozenset({"secretary"}), 3, True)
KEY = "k" * 32
ACT = "https://navitech.example/meetings/act"
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


class FakeSMTP:
    def __init__(self):
        self.sent = []

    def send_message(self, msg):
        self.sent.append(msg)

    def quit(self):
        pass


@pytest.fixture
def mail(meeting_db, monkeypatch):
    """Finalized minutes whose assignment went out; returns the sent messages by kind. The K store starts empty
    (its sample meetings and directory name the same people)."""
    monkeypatch.setattr(STORE, "seed", None)
    emails = {"김민수": "ceo@example.com", "박지훈": "staff@example.com"}
    monkeypatch.setattr(outbox, "lookup_email", lambda name: (emails[name], f"sub-{name}") if name in emails else None)
    monkeypatch.setenv("PRINCIPAL_SIGNING_KEY", KEY)
    monkeypatch.setenv("MEETINGS_ACTION_URL", ACT + "/")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    draft = save_minutes(store=STORE, viewer=SECRETARY, **MINUTES)
    save_minutes(store=STORE, viewer=SECRETARY, **MINUTES, meeting_id=draft["meeting_id"], finalize=True)
    smtp = FakeSMTP()
    with STORE.connect() as conn:
        deliver_approved(conn, connect=lambda cfg: smtp)
    return {"meeting_id": draft["meeting_id"], "sent": smtp.sent}


def _token(msg) -> str:
    [token] = set(re.findall(re.escape(ACT) + r"/([\w.\-]+)\?s=", msg.get_body(("html",)).get_content()))
    return token


def test_a_link_names_one_item_and_person_until_it_expires(monkeypatch):
    monkeypatch.setenv("PRINCIPAL_SIGNING_KEY", KEY)
    token = mail_actions.make_token(7, 3, now=1_000_000)
    assert mail_actions.read_token(token, now=1_000_001) == (7, 3)
    forged = "8" + token[1:]  # another item with the same signature
    assert isinstance(mail_actions.read_token(forged, now=1_000_001), str)
    assert "기간" in mail_actions.read_token(token, now=1_000_000 + (mail_actions.ACTION_DAYS + 1) * 86400)
    monkeypatch.setenv("PRINCIPAL_SIGNING_KEY", "x" * 32)  # another key: not ours
    assert isinstance(mail_actions.read_token(token, now=1_000_001), str)


def test_the_owner_reports_progress_from_the_assignment_mail(mail):
    assignment = next(m for m in mail["sent"] if m["Subject"].startswith("[업무 배정]"))
    html = assignment.get_body(("html",)).get_content()
    assert all(f"?s={s}" in html for s in ("done", "in_progress", "blocked")) and "완료했습니다" in html
    assert "진행 상황 알리기" in assignment.get_body(("plain",)).get_content()  # the plain part carries the links too
    minutes = next(m for m in mail["sent"] if m["Subject"].startswith("[회의록]"))
    assert minutes.get_body(("html",)) is None  # minutes mail has no buttons
    token = _token(assignment)

    shown = mail_actions.act(store=STORE, token=token)  # opening the link changes nothing
    assert shown["ok"] and shown["item"]["text"] == "협력사별 재고 현황 보고" and shown["item"]["owner"] == "박지훈"
    assert shown["item"]["status"] == "open"
    assert "막혔는지" in mail_actions.act(store=STORE, token=token, status="blocked")["error"]
    assert mail_actions.act(store=STORE, token=token, status="maybe")["ok"] is False

    going = mail_actions.act(store=STORE, token=token, status="in_progress", note="1차 집계 끝")
    assert going["ok"] and going["changed"] and going["owner_email"] == "staff@example.com"
    [owner] = [r for r in control(store=STORE, viewer=SECRETARY)["people"] if r["name"] == "박지훈"]
    last = owner["items"][0]["last"]  # the desk shows the owner's latest report under the item
    assert (owner["items"][0]["status"], last["note"], last["by"]) == ("in_progress", "메일로 보고: 1차 집계 끝", "박지훈")
    done = mail_actions.act(store=STORE, token=token, status="done")
    assert done["item"]["status_label"] == "완료"
    with STORE.connect() as conn:
        item = conn.execute("SELECT status, completed_at FROM action_items WHERE code = %s", (done["item"]["id"],)).fetchone()
        notes = [r["note"] for r in conn.execute(
            "SELECT u.note, u.by_email FROM action_item_updates u JOIN action_items a ON a.id = u.action_item_id "
            "WHERE a.code = %s ORDER BY u.id", (done["item"]["id"],))]
    assert item["status"] == "done" and item["completed_at"] is not None
    assert notes == ["메일로 보고: 1차 집계 끝", "메일로 보고"]

    log = [e["text"] for e in control(store=STORE, viewer=SECRETARY)["log"]]
    assert "진행 보고 — 박지훈: 완료 · 협력사별 재고 현황 보고 (메일)" in log
    assert "진행 보고 — 박지훈: 진행 중 · 협력사별 재고 현황 보고 (메일) — 1차 집계 끝" in log


def test_the_link_stops_working_when_the_item_gets_another_owner(mail):
    token = _token(next(m for m in mail["sent"] if m["Subject"].startswith("[업무 배정]")))
    code = mail_actions.act(store=STORE, token=token)["item"]["id"]
    update_action_item(store=STORE, viewer=SECRETARY, item_id=code, owner="김민수")
    assert "담당자가 바뀌어" in mail_actions.act(store=STORE, token=token, status="done")["error"]


def test_without_a_public_address_mail_goes_out_without_buttons(meeting_db, monkeypatch):
    monkeypatch.setattr(outbox, "lookup_email", lambda name: ("staff@example.com", None) if name == "박지훈" else None)
    monkeypatch.setenv("PRINCIPAL_SIGNING_KEY", KEY)
    monkeypatch.delenv("MEETINGS_ACTION_URL", raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    draft = save_minutes(store=STORE, viewer=SECRETARY, **MINUTES)
    save_minutes(store=STORE, viewer=SECRETARY, **MINUTES, meeting_id=draft["meeting_id"], finalize=True)
    smtp = FakeSMTP()
    with STORE.connect() as conn:
        deliver_approved(conn, connect=lambda cfg: smtp)
    assignment = next(m for m in smtp.sent if m["Subject"].startswith("[업무 배정]"))
    assert assignment.get_body(("html",)) is None and "진행 상황 알리기" not in assignment.get_body(("plain",)).get_content()
