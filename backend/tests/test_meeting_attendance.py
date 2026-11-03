"""출석 확인(QR)과 참석자 확인: 녹음만으로는 모르는 '말하지 않은 참석자', 회의마다 따로인 목소리 라벨('화자 A'),
목소리 라벨의 이름 정하기와 목소리 학습, '각자' 할 일 나누기, 이전 회의에서 이어지는 일."""

import re

import pytest
from fastapi.testclient import TestClient

from app.library.workflows import attendance, meeting_minutes, outbox
from app.library.workflows.attendance import WINDOW_SECONDS, current_window, window_token
from app.library.workflows.meeting_minutes import save_minutes
from app.library.workflows.meeting_store import MIGRATIONS_DIR, Viewer
from app.runtime import principal as principal_mod
from app.runtime.schemas import Principal
from app.systems.group import MEETING_STORE as STORE
from app.systems.group.app import app

UPLOADER = Viewer("secretary@example.com", "이서연", frozenset({"secretary"}), 3, True, "kc-seoyeon")
MINSU = Viewer("minsu@example.com", "김민수", frozenset({"staff"}), 2, True, "kc-minsu")
YUJIN = Viewer("yujin@example.com", "최유진", frozenset({"staff"}), 2, True, "kc-yujin")
JIHOON = Viewer("jihoon@example.com", "박지훈", frozenset({"staff"}), 2, True, "kc-jihoon")

TRANSCRIPT = {
    "id": "TR-20261030-AAA111", "source_name": "캡스톤_1030.m4a", "duration": 300.0, "engine": "local",
    "turns": [
        {"start": 1.0, "end": 40.0, "speaker": "이서연", "score": 0.8, "text": "오늘은 데이터 저장 방식을 정합시다."},
        {"start": 41.0, "end": 90.0, "speaker": "화자 A", "score": 0.2, "text": "그래프로 관리하고 기록은 시맨틱 서치로 가져오죠."},
        {"start": 91.0, "end": 120.0, "speaker": "김민수", "score": 0.7, "text": "멘토 미팅 질문은 각자 준비합시다."},
        {"start": 121.0, "end": 160.0, "speaker": "화자 A", "score": 0.2, "text": "제가 저장 구조 초안을 만들겠습니다."},
    ],
}

MINUTES = dict(
    title="캡스톤 팀 회의",
    date="2026-10-30",
    summary="화자 A가 그래프와 시맨틱 서치로 나누는 저장 구조를 제안했다.",
    attendees=[{"name": "이서연"}, {"name": "김민수 (팀장)"}, {"name": "화자 A"}],
    decisions=[{"kind": "opinion", "speaker": "화자 A", "statement": "변하지 않는 것은 그래프로, 쌓이는 기록은 시맨틱 서치로"}],
    action_items=[{"text": "저장 구조 초안", "owner": "화자 A", "due": "2026-11-10"},
                  {"text": "멘토 미팅 질문 준비"}],
)


@pytest.fixture
def recording(meeting_db, monkeypatch):
    monkeypatch.setattr(meeting_minutes, "fetch_transcript", lambda ref: TRANSCRIPT)
    monkeypatch.setattr(outbox, "lookup_email", lambda name: None)
    learned = []

    def learn(ref, label, **kw):
        learned.append((ref, label, kw["user_id"], kw["email"], kw["name"]))
        return "learned"

    monkeypatch.setattr(attendance, "learn_voice", learn)
    return learned


def save(viewer=UPLOADER, **overrides):
    return save_minutes(store=STORE, viewer=viewer, **{**MINUTES, "transcript_id": TRANSCRIPT["id"], **overrides})


def opened():
    out = attendance.open_session(store=STORE, viewer=UPLOADER, source_name="캡스톤_1030.m4a")
    assert out["ok"] and re.fullmatch(r"CHK-[A-Z2-9]{6}", out["code"]) and len(out["token"]) == 8
    return out


def join(viewer, session, **kw):
    return attendance.join(store=STORE, viewer=viewer, code=session["code"], token=session["token"], **kw)


# ---------------------------------------------------------------------------
# 목소리 라벨은 그 회의 안에서만 한 사람
# ---------------------------------------------------------------------------


def test_a_voice_label_names_one_person_of_one_meeting(recording):
    first = save()
    second = save(title="다음 회의", date="2026-11-06", transcript_id=None,
                  action_items=[{"text": "다른 일", "owner": "화자 A", "due": "2026-11-12"}])
    with STORE.connect() as conn:
        rows = conn.execute(
            """SELECT m.code, a.owner_person_id, p.speaker_meeting_id = m.id AS scoped FROM action_items a
               JOIN meetings m ON m.id = a.meeting_id JOIN people p ON p.id = a.owner_person_id WHERE p.display_name = '화자 A'"""
        ).fetchall()
    assert {r["code"] for r in rows} == {first["meeting_id"], second["meeting_id"]}
    assert len({r["owner_person_id"] for r in rows}) == 2 and all(r["scoped"] for r in rows)
    assert not [e for e in first["new_directory_entries"] if "화자" in e]  # a voice is not a new directory entry


def test_the_migration_splits_a_label_two_meetings_shared(recording):
    first, second = save(), save(title="다음 회의", date="2026-11-06", transcript_id=None)
    block = re.search(r"DO \$\$.*?END \$\$;", (MIGRATIONS_DIR / "005_attendance.sql").read_text(encoding="utf-8"), re.S).group(0)
    with STORE.connect() as conn:  # what deployments had before 005: one '화자 A' row for both meetings
        shared = conn.execute("INSERT INTO people (display_name) VALUES ('화자 A') RETURNING id").fetchone()["id"]
        conn.execute("UPDATE meeting_participants SET person_id = %s WHERE person_id IN (SELECT id FROM people WHERE display_name = '화자 A' AND id <> %s)",
                     (shared, shared))
        conn.execute("UPDATE action_items SET owner_person_id = %s WHERE owner_person_id IN (SELECT id FROM people WHERE display_name = '화자 A' AND id <> %s)",
                     (shared, shared))
        conn.execute("DELETE FROM people WHERE display_name = '화자 A' AND id <> %s", (shared,))
        conn.execute(block)
        rows = conn.execute(
            """SELECT m.code, p.id, p.speaker_meeting_id = m.id AS scoped FROM meeting_participants mp
               JOIN meetings m ON m.id = mp.meeting_id JOIN people p ON p.id = mp.person_id WHERE p.display_name = '화자 A'"""
        ).fetchall()
    assert {r["code"] for r in rows} == {first["meeting_id"], second["meeting_id"]}
    assert len({r["id"] for r in rows}) == 2 and all(r["scoped"] for r in rows)


# ---------------------------------------------------------------------------
# 출석 확인 (QR)
# ---------------------------------------------------------------------------


def test_the_qr_code_changes_every_window_and_recent_ones_are_accepted(meeting_db):
    s = opened()
    with STORE.connect() as conn:
        secret = conn.execute("SELECT secret FROM checkin_sessions WHERE code = %s", (s["code"],)).fetchone()["secret"]
    now = current_window() * WINDOW_SECONDS + 1.0
    old = window_token(secret, s["code"], current_window(now) - 5)  # 2½ minutes ago
    assert window_token(secret, s["code"], current_window(now)) != window_token(secret, s["code"], current_window(now) - 1)
    stale = attendance.join(store=STORE, viewer=MINSU, code=s["code"], token=old, now=now)
    assert not stale["ok"] and stale["expired"]
    fresh = window_token(secret, s["code"], current_window(now) - 2)
    assert attendance.join(store=STORE, viewer=MINSU, code=s["code"], token=fresh, now=now)["ok"]
    # Only the person who opened it gets the code to show; anyone else is told nothing.
    assert attendance.session_status(store=STORE, viewer=UPLOADER, code=s["code"])["count"] == 1
    assert not attendance.session_status(store=STORE, viewer=MINSU, code=s["code"])["ok"]


def test_after_closing_nobody_new_checks_in_but_consent_can_still_change(meeting_db):
    s = opened()
    assert join(MINSU, s)["ok"]
    attendance.close_session(store=STORE, viewer=UPLOADER, code=s["code"])
    assert not join(YUJIN, s)["ok"]
    again = attendance.join(store=STORE, viewer=MINSU, code=s["code"], token=None, voice_consent=True)
    assert again["ok"] and again["voice_consent"]


def test_people_who_checked_in_are_attendees_even_if_they_never_spoke(recording):
    s = opened()
    for v in (UPLOADER, MINSU, YUJIN):
        assert join(v, s)["ok"]
    out = save(checkin_code=s["code"])
    people = {p["name"]: p for p in out["attendance"]["people"]}
    assert set(people) == {"이서연", "김민수 (팀장)", "최유진"}  # '김민수 (팀장)' the model wrote is the account that checked in
    assert people["최유진"]["checked_in"] and not people["최유진"]["spoke"]
    assert out["attendance"]["silent"] == ["최유진"]
    assert [v["label"] for v in out["attendance"]["unknown_speakers"]] == ["화자 A"]
    # one unnamed voice, one person who checked in and said nothing: probably them
    assert out["attendance"]["suggestion"] == {"speaker": "화자 A", "name": "최유진", "email": "yujin@example.com"}
    with STORE.connect() as conn:
        emails = {r["email"] for r in conn.execute(
            """SELECT p.email FROM meeting_participants mp JOIN people p ON p.id = mp.person_id JOIN meetings m ON m.id = mp.meeting_id
               WHERE m.code = %s AND mp.checked_in_at IS NOT NULL""", (out["meeting_id"],))}
        assert emails == {"secretary@example.com", "minsu@example.com", "yujin@example.com"}
        assert conn.execute("SELECT count(*) AS n FROM people WHERE display_name = '김민수'").fetchone()["n"] == 1  # merged, not two


def test_minutes_from_a_recording_take_the_uploaders_latest_check_in_without_the_code(recording):
    s = opened()
    join(YUJIN, s)
    out = save()  # the model left out checkin_code
    assert "최유진" in {p["name"] for p in out["attendance"]["people"]}
    late = attendance.join(store=STORE, viewer=JIHOON, code=s["code"], token=attendance.session_status(
        store=STORE, viewer=UPLOADER, code=s["code"])["token"])
    assert late["ok"] and late["meeting"] == "캡스톤 팀 회의"
    with STORE.connect() as conn:  # scanned after the minutes were saved: an attendee right away
        m = conn.execute("SELECT id FROM meetings WHERE code = %s", (out["meeting_id"],)).fetchone()["id"]
        assert "박지훈" in {p["name"] for p in attendance.attendance_view(conn, m)["people"]}


# ---------------------------------------------------------------------------
# 회의 카드: 목소리의 이름 정하기, 참석자 더하기 · 빼기, '각자' 할 일 나누기
# ---------------------------------------------------------------------------


def test_naming_a_voice_moves_its_statements_tasks_and_text_and_learns_it_with_consent(recording):
    s = opened()
    join(UPLOADER, s)
    join(YUJIN, s, voice_consent=True)
    out = save(checkin_code=s["code"])
    done = attendance.assign_speaker(store=STORE, viewer=UPLOADER, meeting_id=out["meeting_id"], speaker="화자 A",
                                     person={"name": "최유진", "email": "yujin@example.com"})
    assert done["ok"] and done["voice"] == "learned"
    assert recording == [(TRANSCRIPT["id"], "화자 A", "kc-yujin", "yujin@example.com", "최유진")]
    with STORE.connect() as conn:
        mid = conn.execute("SELECT id FROM meetings WHERE code = %s", (out["meeting_id"],)).fetchone()["id"]
        record = meeting_minutes.minutes_record(conn, mid)
        segs = conn.execute("""SELECT p.email, s.speaker_confirmed FROM transcript_segments s JOIN people p ON p.id = s.person_id
                               WHERE s.speaker_label = '화자 A'""").fetchall()
        view = attendance.attendance_view(conn, mid)
        left = conn.execute("SELECT count(*) AS n FROM people WHERE display_name = '화자 A'").fetchone()["n"]
    assert record["decisions"][0]["speaker"] == "최유진"
    assert [a["owner"] for a in record["action_items"] if a["text"] == "저장 구조 초안"] == ["최유진"]
    assert record["summary"].startswith("최유진가 그래프와")
    assert segs and all(r["email"] == "yujin@example.com" and r["speaker_confirmed"] for r in segs)
    assert view["unknown_speakers"] == [] and view["silent"] == [] and left == 0


def test_without_consent_the_voice_is_named_but_not_learned(recording):
    s = opened()
    join(YUJIN, s)
    out = save(checkin_code=s["code"])
    done = attendance.assign_speaker(store=STORE, viewer=UPLOADER, meeting_id=out["meeting_id"], speaker="화자 A",
                                     person={"name": "최유진", "email": "yujin@example.com"})
    assert done["ok"] and done["voice"] == "no_consent" and recording == []


def test_final_minutes_are_corrected_not_edited_from_the_card(recording):
    out = save(action_items=[{"text": "저장 구조 초안", "owner": "이서연", "due": "2026-11-10"}])
    with STORE.connect() as conn:
        conn.execute("UPDATE minutes SET status = 'final', finalized_by = 'secretary@example.com', finalized_at = now() WHERE meeting_id = (SELECT id FROM meetings WHERE code = %s)", (out["meeting_id"],))
    refused = attendance.assign_speaker(store=STORE, viewer=UPLOADER, meeting_id=out["meeting_id"], speaker="화자 A",
                                        person={"name": "최유진"})
    assert not refused["ok"] and "정정" in refused["error"]


def test_adding_a_silent_attendee_and_splitting_everyones_task(recording):
    out = save()
    added = attendance.add_attendee(store=STORE, viewer=UPLOADER, meeting_id=out["meeting_id"],
                                    person={"name": "최유진", "email": "yujin@example.com"})
    assert added["ok"]
    with STORE.connect() as conn:
        code = conn.execute("SELECT code FROM action_items WHERE text = '멘토 미팅 질문 준비'").fetchone()["code"]
    split = attendance.split_item(store=STORE, viewer=UPLOADER, item_id=code)
    assert split["ok"] and sorted(i["owner"] for i in split["items"]) == ["김민수 (팀장)", "이서연", "최유진"]  # not the voice label
    with STORE.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM action_items WHERE code = %s", (code,)).fetchone()["n"] == 0
    blocked = attendance.remove_attendee(store=STORE, viewer=UPLOADER, meeting_id=out["meeting_id"], person={"name": "최유진"})
    assert not blocked["ok"] and "할 일" in blocked["error"]


def test_recall_brings_back_what_these_attendees_left_open(recording):
    earlier = save_minutes(store=STORE, viewer=UPLOADER, title="10/13 회의", date="2026-10-13", summary="역할 분담",
                           attendees=[{"name": "이서연"}, {"name": "김민수 (팀장)"}],
                           decisions=[{"kind": "issue", "statement": "모델을 무엇으로 할지 정하지 못함"}],
                           action_items=[{"text": "에이전트별 데이터 조사", "owner": "김민수 (팀장)", "due": "2026-10-20"}])
    out = save()
    items = out["recall"]["open_items"]
    assert [i["meeting"]["id"] for i in items] == [earlier["meeting_id"]] and items[0]["overdue_days"] > 0
    assert [i["statement"] for i in out["recall"]["open_issues"]] == ["모델을 무엇으로 할지 정하지 못함"]


def test_desk_routes_check_in_and_the_meeting_card_shows_attendance(recording):
    client = TestClient(app)

    def call(path, principal, **body):
        res = client.post(f"/api/desk/{path}", json={"principal": principal, **body})
        assert res.status_code == 200, res.text
        return res.json()

    uploader = {"email": UPLOADER.email, "name": UPLOADER.name, "groups": ["secretary"], "clearance": 3, "user_id": "kc-seoyeon"}
    yujin = {"email": YUJIN.email, "name": YUJIN.name, "groups": ["staff"], "clearance": 2, "user_id": "kc-yujin"}
    s = call("checkin", uploader, action="open", source_name="캡스톤_1030.m4a")
    joined = call("checkin", yujin, action="join", code=s["code"], token=s["token"], voice_consent=True)
    assert joined["ok"] and joined["voice_consent"]
    out = save(checkin_code=s["code"])
    card = call("meeting", uploader, meeting_id=out["meeting_id"])
    assert card["attendance"]["suggestion"]["name"] == "최유진" and card["recall"] == {"open_items": [], "open_issues": []}
    named = call("attendance", uploader, action="assign", meeting_id=out["meeting_id"], speaker="화자 A",
                 person={"name": "최유진", "email": "yujin@example.com"})
    assert named["ok"] and named["voice"] == "learned"


def test_the_signed_principal_carries_the_keycloak_id():
    key = "k" * 32
    token = principal_mod.sign(Principal(email="a@example.com", name="가", clearance=2, user_id="kc-1"), key)
    assert principal_mod.verify(token, key).user_id == "kc-1"
    assert principal_mod.verify(principal_mod.sign(Principal(email="a@example.com", clearance=2), key), key).user_id is None


def test_transcript_correction_knows_who_checked_in_then_the_directory(meeting_db):
    from app.library.workflows.transcript_correction import known_names

    s = attendance.open_session(store=STORE, viewer=UPLOADER, source_name="회의.m4a")
    attendance.join(store=STORE, viewer=YUJIN, code=s["code"], token=s["token"])
    with STORE.connect() as conn:
        names = known_names(conn, s["code"])
        directory = known_names(conn)
    assert names[0] == "최유진" and directory and set(directory) <= set(names)  # 출석한 사람 먼저, 그다음 임직원 디렉터리



def test_check_in_turned_off_links_no_session(recording, monkeypatch):
    s = attendance.open_session(store=STORE, viewer=UPLOADER, source_name="회의.m4a")
    attendance.join(store=STORE, viewer=YUJIN, code=s["code"], token=s["token"])
    monkeypatch.setenv("CHECKIN_ENABLED", "0")
    out = save(checkin_code=s["code"])
    assert out["saved"] and not any(p.get("checked_in") for p in out["attendance"]["people"])
