"""The meeting desk (/api/desk/*): people read the meeting store and press review / done / resend
themselves, with the same principal and permission rules as the meeting Tools."""

import pytest
from fastapi.testclient import TestClient

from app.library.workflows import outbox
from app.library.workflows.meeting_minutes import save_minutes
from app.library.workflows.meeting_store import Viewer
from app.systems.group import MEETING_STORE as STORE
from app.systems.group.app import app

SECRETARY = {"email": "secretary@example.com", "name": "이서연", "groups": ["secretary"], "clearance": 3}
STAFF = {"email": "staff@example.com", "name": "박지훈", "groups": ["staff"], "clearance": 3}
WRITER = Viewer("secretary@example.com", "이서연", frozenset({"secretary"}), 3, True)

MINUTES = dict(
    title="공급망 점검회의",
    date="2026-10-24",
    summary="2차 협력사 단가 조정안을 검토하고 재고 기준을 정했다.",
    body="박지훈: 경쟁사와 단가를 맞추자는 얘기가 있었으나 결론 없음",
    classification=2,
    attendees=[{"name": "김민수", "title": "CEO", "role": "chair"}, {"name": "박지훈", "title": "CFO"},
               {"name": "이서연", "role": "secretary"}],
    decisions=[{"kind": "decision", "statement": "안전재고를 4주분으로 늘린다"}],
    action_items=[{"text": "협력사별 재고 현황 보고", "owner": "박지훈", "due": "2026-11-20"}],
)


@pytest.fixture
def desk(meeting_db, monkeypatch):
    emails = {"김민수": "ceo@example.com", "박지훈": "staff@example.com", "이서연": "secretary@example.com"}
    monkeypatch.setattr(outbox, "lookup_email", lambda name: (emails[name], f"sub-{name}") if name in emails else None)
    client = TestClient(app)

    def call(path, principal=SECRETARY, **body):
        res = client.post(f"/api/desk/{path}", json={"principal": principal, **body})
        assert res.status_code == 200, res.text
        return res.json()

    return call


def test_desk_review_finalize_mail_and_items(desk):
    draft = save_minutes(store=STORE, viewer=WRITER, **MINUTES)
    code = draft["meeting_id"]

    first = desk("overview")
    assert {"mail_pending", "mail_needs_address", "flags_open", "my_items", "my_overdue"} <= set(first["counts"])
    assert first["minutes"][0] == {**first["minutes"][0], "id": code, "status": "draft", "open_flags": 1, "decisions": 1, "items": 1}

    flags = desk("flags")["flags"]
    assert [f["meeting"]["id"] for f in flags] == [code]
    review = [{"id": flags[0]["id"], "status": "cleared"}]
    denied = desk("flags", STAFF, action="review", meeting_id=code, reviews=review)
    assert denied["ok"] is False and "의장 · 간사" in denied["error"]
    done = desk("flags", action="review", meeting_id=code, reviews=review)
    assert done["ok"] is True and done["flags"][0]["status"] == "cleared"

    detail = desk("meeting", meeting_id=code)
    assert detail["meeting"]["id"] == code and detail["can_confirm"] is True
    assert [p["name"] for p in detail["participants"]][0] == "김민수 (CEO)"  # the chair first
    assert desk("meeting", STAFF, meeting_id=code)["can_confirm"] is False

    save_minutes(store=STORE, viewer=WRITER, meeting_id=code, finalize=True, **MINUTES)  # finalizing sends the mail
    mails = desk("mail")["mails"]
    assert mails and all(m["status"] == "approved" for m in mails)  # ready to go: nobody approves it
    assert desk("overview")["counts"]["mail_pending"] == len(mails)
    assert desk("mail", STAFF, action="reject", mail_ids=[mails[0]["id"]])["ok"] is False
    cancelled = desk("mail", action="reject", mail_ids=[mails[0]["id"]])
    assert cancelled["ok"] is True and cancelled["done"] == [mails[0]["id"]]

    mine = desk("items", STAFF, view="mine")["items"]
    assert [i["text"] for i in mine] == ["협력사별 재고 현황 보고"]
    done_before = desk("items", view="all")["summary"]["done"]  # the store's seed has finished items too
    updated = desk("items", STAFF, item_id=mine[0]["id"], status="done", note="보고 완료")
    assert updated["updated"] is True
    assert desk("items", view="all")["summary"]["done"] == done_before + 1
    assert desk("items", STAFF, view="mine")["items"] == []


def test_desk_schedule_create_update_cancel(desk):
    from datetime import date, timedelta

    day = (date.today() + timedelta(days=7)).isoformat()
    made = desk("schedule", action="create", title="공급망 후속회의", starts_at=f"{day} 14:00", ends_at=f"{day} 15:00",
                location="본사 12층", attendees=["김민수", "박지훈"], agenda=["재고 보고"], classification=2)
    assert made["ok"] is True and [m["status"] for m in made["mail_queued"]] == ["approved", "approved"]
    code = made["meeting"]["id"]
    assert code in [m["id"] for m in desk("overview")["upcoming"]]
    moved = desk("schedule", action="update", meeting_id=code, starts_at=f"{day} 16:00", agenda=["단가 협상"])
    assert moved["ok"] is True and moved["meeting"]["starts_at"].endswith("16:00")
    assert moved["meeting"]["ends_at"].endswith("17:00")  # 시작만 옮기면 길이(1시간)는 그대로
    # 회의록이 아직 없는 예약된 회의도 한 건으로 열린다 (채팅 카드가 일정을 보여 줌)
    scheduled = desk("meeting", meeting_id=code)["scheduled"]
    assert scheduled["starts_at"].endswith("16:00") and [a["title"] for a in scheduled["agenda"]] == ["재고 보고", "단가 협상"]
    assert [a["title"] for a in moved["meeting"]["agenda"]] == ["재고 보고", "단가 협상"]
    assert desk("schedule", action="cancel", meeting_id=code)["ok"] is True
    assert code not in [m["id"] for m in desk("overview")["upcoming"]]
    assert desk("schedule", action="list")["ok"] is False  # 목록은 overview가 보여 줌


def test_desk_assigns_owner_and_due_and_mails_the_owner_once_the_minutes_are_final(desk):
    plain = {**MINUTES, "body": None}  # 위험 발언 검토 없이 확정되도록
    draft = save_minutes(store=STORE, viewer=WRITER, **{**plain, "action_items": [{"text": "협력사별 재고 현황 보고"}]})
    item = f"{draft['meeting_id']}-A1"
    assigned = desk("items", item_id=item, owner="박지훈", due="2026-11-20")
    assert assigned["updated"] and assigned["item"]["owner"] == "박지훈 (CFO)" and assigned["mail_queued"] == []  # 초안: 확정 때 보냄
    final = save_minutes(store=STORE, viewer=WRITER, meeting_id=draft["meeting_id"], finalize=True,
                         **{**plain, "action_items": [{"id": item, "text": "협력사별 재고 현황 보고", "owner": "박지훈", "due": "2026-11-20"}]})
    assert final["finalized"] and [m["kind"] for m in final["mail_queued"]].count("assignment") == 1
    moved = desk("items", item_id=item, owner="김민수")
    assert [(m["kind"], m["to"]) for m in moved["mail_queued"]] == [("assignment", "김민수 (CEO)")]
    assert desk("items", item_id=item, owner="김민수")["mail_queued"] == []  # 같은 배정은 다시 보내지 않음


def test_desk_finds_minutes_by_words_and_status(desk):
    save_minutes(store=STORE, viewer=WRITER, **{**MINUTES, "body": None, "topics": ["재고", "협력사"]})
    other = save_minutes(store=STORE, viewer=WRITER, **{**MINUTES, "title": "뉴로엣지 인수 점검", "date": "2026-10-20", "body": None,
                                                        "summary": "가격 상한을 정했다.", "action_items": [], "topics": ["M&A"]})
    every = desk("minutes")
    assert [m["title"] for m in every["minutes"][:2]] == ["공급망 점검회의", "뉴로엣지 인수 점검"]  # 최근 순 (그 뒤는 fixture)
    assert every["counts"]["total"] == len(every["minutes"]) and every["more"] is False
    assert every["minutes"][1]["id"] == other["meeting_id"]
    found = desk("minutes", query="협력사 재고")  # 낱말 모두 (주제에서)
    assert [m["title"] for m in found["minutes"]] == ["공급망 점검회의"] and found["counts"]["draft"] == 1
    assert desk("minutes", query=other["meeting_id"], status="final")["minutes"] == []  # 초안이라 확정 목록에 없음
    assert [m["status"] for m in desk("minutes", query=other["meeting_id"], status="draft")["minutes"]] == ["draft"]
    assert desk("minutes", query="100%")["minutes"] == []  # 검색어의 %는 글자 그대로
    assert "error" in desk("minutes", status="held")


def test_desk_without_a_user_sees_nothing(desk, monkeypatch):
    monkeypatch.setenv("MEETINGS_ANONYMOUS_CLEARANCE", "0")
    save_minutes(store=STORE, viewer=WRITER, **MINUTES)
    anonymous = desk("overview", None)
    assert anonymous["minutes"] == [] and anonymous["counts"]["flags_open"] == 0
    assert desk("items", None, view="mine")["error"]


def _level(code):
    with STORE.connect() as conn:
        row = conn.execute("SELECT classification, topics FROM meetings WHERE code = %s", (code,)).fetchone()
    return row["classification"], list(row["topics"])


def test_finalizing_or_saving_again_keeps_the_classification_and_topics_unless_given(desk):
    from app.library.workflows import meeting_desk

    minutes = {**MINUTES, "body": None, "classification": 3, "topics": ["공급망", "안전재고"]}
    code = save_minutes(store=STORE, viewer=WRITER, **minutes)["meeting_id"]
    assert meeting_desk.finalize(store=STORE, viewer=WRITER, meeting_id=code)["finalized"]
    assert _level(code) == (3, ["공급망", "안전재고"])  # the desk sends neither: kept, not reset to 2 and []
    again = {k: v for k, v in MINUTES.items() if k not in ("body", "classification")}
    out = save_minutes(store=STORE, viewer=WRITER, **again, meeting_id=code, correction_reason="요약 보완", finalize=True)
    assert out["status"] == "final", out
    assert _level(code) == (3, ["공급망", "안전재고"])  # a correction that leaves them out keeps them
    save_minutes(store=STORE, viewer=WRITER, **again, meeting_id=code, correction_reason="등급 조정", classification=2, topics=["재고"],
                 finalize=True)
    assert _level(code) == (2, ["재고"])  # given explicitly: changed
    new = save_minutes(store=STORE, viewer=WRITER, **{**again, "title": "새 회의"})
    assert _level(new["meeting_id"]) == (2, [])  # a new meeting without them: 2 and none, as before
