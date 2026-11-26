"""회의가 끝난 뒤의 결정: 전제 · 조건과 함께 저장된 결정, 그 전제를 흔드는 뉴스(한 번만 판단, 모델이 답하지 않으면 낱말로 표시),
결정에 관계된 사람에게 가는 알림, 새 계획의 금액이 결정을 넘는지, 재검토 회의안, 사람이 '문제없음'으로 닫기."""

import json
from datetime import date

import pytest

from app.library.tools import run_decision_review as review_tool
from app.library.workflows import decision_watch as dw
from app.library.workflows import meeting_minutes, outbox
from app.library.workflows.meeting_desk import overview
from app.library.workflows.meeting_control import control
from app.library.workflows.meeting_minutes import save_minutes
from app.library.workflows.meeting_schedule import meeting_schedule
from app.library.workflows.meeting_store import KST, Viewer
from app.systems.group import MEETING_STORE as STORE
from app.systems.group.registry import tools_for

SECRETARY = Viewer("secretary@example.com", "정전략", frozenset({"secretary"}), 3, True)

TRANSCRIPT = {
    "id": "TR-20261103-HBM001", "source_name": "HBM_투심위.wav", "duration": 120.0, "engine": "local",
    "turns": [
        {"start": 5.0, "end": 30.0, "speaker": "박지훈 (CFO)", "score": 0.8,
         "text": "이 투자는 HBM 단가가 내년 말까지 지금 수준을 유지한다는 전제입니다. 15퍼센트 넘게 떨어지면 다시 봐야 합니다."},
        {"start": 31.0, "end": 50.0, "speaker": "이서연 (CTO)", "score": 0.8, "text": "수율이 내년 3월까지 85퍼센트에 도달해야 합니다."},
        {"start": 51.0, "end": 70.0, "speaker": "김민수 (CEO)", "score": 0.8, "text": "HBM 라인 증설은 9천억 원 한도로 승인하겠습니다."},
    ],
}

MINUTES = dict(
    title="HBM 증설 투자 심의",
    date="2026-11-03",
    summary="HBM 라인 증설을 9천억 원 한도로 승인했다.",
    attendees=[{"name": "김민수", "title": "CEO", "role": "chair"}, {"name": "박지훈", "title": "CFO"},
               {"name": "이서연", "title": "CTO"}],
    topics=["HBM", "증설", "투자"],
    decisions=[{"kind": "decision", "statement": "HBM 라인 증설을 9천억 원 한도로 승인", "source_time": "00:51",
                "premises": [
                    {"kind": "premise", "statement": "HBM 단가가 내년 말까지 현재 수준 유지", "review_when": "단가가 15% 넘게 떨어지면 투자 규모 재검토",
                     "watch": ["HBM", "단가"], "speaker": "박지훈 (CFO)", "source_time": "00:05"},
                    {"kind": "condition", "statement": "내년 3월까지 수율 85% 달성", "due": "2027-03-31", "speaker": "이서연 (CTO)",
                     "watch": ["수율"]}]}],
    action_items=[{"text": "세부 투자 계획 제출", "owner": "이서연", "due": "2026-11-20"}],
    transcript_id=TRANSCRIPT["id"],
)

PRICE_CUT = {"id": "NEWS-T-1", "date": "2026-11-03", "source": "테크데일리 (가상)", "headline": "경쟁사 HBM3E 공급가 18% 인하",
             "summary": "경쟁사가 HBM3E 공급가를 18% 낮췄다.", "companies": ["경쟁사"], "key_facts": ["공급가 18% 인하"]}
OLD_NEWS = {**PRICE_CUT, "id": "NEWS-T-0", "date": "2026-10-01"}  # before the decision: already known at the meeting
UNRELATED = {"id": "NEWS-T-2", "date": "2026-11-03", "source": "산업신문 (가상)", "headline": "K물산 노조 파업 예고",
             "summary": "임금협상 결렬", "companies": ["K물산"], "key_facts": []}


class Model:
    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def __call__(self, system, user):
        self.prompts.append(user)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)


@pytest.fixture
def decided(meeting_db, monkeypatch):
    monkeypatch.setattr(meeting_minutes, "fetch_transcript", lambda ref: TRANSCRIPT)
    monkeypatch.setattr(outbox, "lookup_email", lambda name: None)
    out = save_minutes(store=STORE, viewer=SECRETARY, **MINUTES)
    assert out["saved"] and not [w for w in out.get("warnings", []) if "전제" in w], out.get("warnings")
    final = save_minutes(store=STORE, viewer=SECRETARY, **{**MINUTES, "meeting_id": out["meeting_id"], "finalize": True})
    assert final["finalized"], final.get("warnings")
    return out["meeting_id"]


def scan(articles, ask):
    with STORE.connect() as conn:
        return dw.scan_articles(conn, articles, ask)


def test_amounts_read_korean_money():
    assert dw.amounts("총 1조 1천억 원, 1차 9천억, 2.5조, 9,000억, 900억 원") == [11000, 9000, 25000, 9000, 900]
    assert dw.won(11000) == "1조 1,000억 원" and dw.won(9000) == "9,000억 원" and dw.won(20000) == "2조 원"


def test_a_decision_keeps_its_premises_with_the_statements_they_came_from(decided):
    found = dw.review_decisions("HBM 증설 계속 가도 돼?", ["HBM"], store=STORE, viewer=SECRETARY)
    [d] = found["decisions"]
    assert d["ref"] == f"{decided}-D1" and d["source"] == "[00:51] 김민수 (CEO)"
    premise, condition = d["premises"]
    assert premise == {"kind": "premise", "statement": "HBM 단가가 내년 말까지 현재 수준 유지", "status": "holding",
                       "review_when": "단가가 15% 넘게 떨어지면 투자 규모 재검토", "speaker": "박지훈 (CFO)", "source": "[00:05] 박지훈 (CFO)",
                       "watch": ["HBM", "단가"]}
    assert condition["kind"] == "condition" and condition["due"] == "2027-03-31"
    assert "re_review" not in found and found["conflicts"] == []  # nothing shaken, no plan


def test_news_that_breaks_a_premise_is_judged_once_and_mailed_to_the_decisions_people(decided):
    model = Model({"effect": "broken", "reason": "공급가 18% 인하로 재검토 기준(15%)을 넘었다"})
    assert scan([OLD_NEWS, UNRELATED, PRICE_CUT], model) == {"checked": 1, "shaken": 1, "mailed": 2}
    assert "재검토 기준: 단가가 15% 넘게 떨어지면" in model.prompts[0] and "경쟁사 HBM3E 공급가 18% 인하" in model.prompts[0]
    assert scan([PRICE_CUT], Model()) == {"checked": 0, "shaken": 0, "mailed": 0}  # never asked twice
    with STORE.connect() as conn:
        mails = conn.execute("SELECT to_name, subject, body FROM outbox WHERE kind = 'alert' ORDER BY to_name").fetchall()
        [signal] = dw.open_signals(conn, SECRETARY)
    assert [m["to_name"] for m in mails] == ["김민수 (CEO)", "박지훈 (CFO)"]  # chair (made the decision) + who stated the premise
    assert mails[0]["subject"] == f"[전제 변화] HBM 증설 투자 심의 — {decided}-D1" and "공급가 18% 인하로" in mails[0]["body"]
    assert signal["effect"] == "broken" and signal["premise"]["status"] == "broken" and signal["decision"]["ref"] == f"{decided}-D1"
    assert overview(store=STORE, viewer=SECRETARY)["watch"][0]["id"] == signal["id"]


def test_a_model_that_does_not_answer_leaves_the_words_match_for_a_person(decided):
    assert scan([PRICE_CUT], Model(ConnectionError("ollama down"))) == {"checked": 1, "shaken": 1, "mailed": 2}
    with STORE.connect() as conn:
        [signal] = dw.open_signals(conn, SECRETARY)
    assert signal["effect"] == "at_risk" and signal["judged"] == "keywords" and "HBM" in signal["reason"]
    assert scan([{**PRICE_CUT, "id": "NEWS-T-3"}], Model({"effect": "none", "reason": "관계없음"}))["shaken"] == 0


def test_a_plan_past_the_decided_amount_conflicts_and_a_re_review_is_proposed(decided):
    scan([PRICE_CUT], Model({"effect": "broken", "reason": "18% 인하"}))
    # The seeded 투자심의 meeting shares words (K전자, 투자심의) and an amount, but the decision asked about is this one
    found = dw.review_decisions("K전자가 HBM 라인 2개, 총 1조 1천억 원 계획을 보냈어. 지난 투자심의 결정과 맞는지 확인해 줘", [],
                                store=STORE, viewer=SECRETARY)
    assert [d["ref"] for d in found["decisions"]] == [f"{decided}-D1"]
    assert found["conflicts"] == [{"decision": f"{decided}-D1", "decided": "9,000억 원", "proposed": "1조 1,000억 원",
                                   "over_by": "2,000억 원", "note": "결정한 금액을 넘는다 — 그 회의체의 재승인이 필요하다"}]
    signal = found["decisions"][0]["premises"][0]["signals"][0]
    assert signal["headline"] == PRICE_CUT["headline"] and signal["effect"] == "broken"
    plan = found["re_review"]
    assert plan["title"] == "HBM 증설 투자 심의 재검토" and plan["carry_over_from"] == decided
    assert plan["agenda"] == [f"{decided}-D1 전제 재검토: HBM 단가가 내년 말까지 현재 수준 유지 — 경쟁사 HBM3E 공급가 18% 인하",
                              f"새 계획 1조 1,000억 원 승인 여부 — {decided}-D1 결정 9,000억 원 초과"]
    assert plan["attendees"] == ["김민수 (CEO, 의장)", "박지훈 (CFO)", "이서연 (CTO)"]
    # booked: the next look says so instead of proposing it again
    meeting_schedule(store=STORE, viewer=SECRETARY, action="create", title=plan["title"], starts_at="2026-11-10 14:00",
                     attendees=plan["attendees"], agenda=plan["agenda"], today=date(2026, 11, 3))
    again = dw.review_decisions("HBM 증설 계속 가도 돼?", [], store=STORE, viewer=SECRETARY, today=date(2026, 11, 3))
    assert "re_review" not in again and again["scheduled_review"]["starts_at"] == "2026-11-10 14:00"
    assert again["scheduled_review"]["agenda"] == plan["agenda"]


def test_a_condition_due_before_the_meeting_is_questioned(meeting_db, monkeypatch):
    monkeypatch.setattr(meeting_minutes, "fetch_transcript", lambda ref: TRANSCRIPT)
    decision = {**MINUTES["decisions"][0], "premises": [{**MINUTES["decisions"][0]["premises"][1], "due": "2026-03-31"}]}
    out = save_minutes(store=STORE, viewer=SECRETARY, **{**MINUTES, "decisions": [decision]})
    assert any("기한 2026-03-31이 회의 날짜(2026-11-03)보다 앞섬" in w for w in out["warnings"])


def test_people_the_recording_names_by_voice_are_attendees(meeting_db, monkeypatch):
    monkeypatch.setattr(meeting_minutes, "fetch_transcript", lambda ref: TRANSCRIPT)
    chair_only = [{"name": "김민수", "title": "CEO", "role": "chair"}]

    def attendees(code):
        with STORE.connect() as conn:
            return [r["display_name"] for r in conn.execute(
                "SELECT p.display_name FROM meeting_participants mp JOIN people p ON p.id = mp.person_id JOIN meetings m ON m.id = mp.meeting_id "
                "WHERE m.code = %s ORDER BY p.display_name", (code,))]

    out = save_minutes(store=STORE, viewer=SECRETARY, **{**MINUTES, "attendees": chair_only})
    assert attendees(out["meeting_id"]) == ["김민수", "박지훈", "이서연"]
    # A later save (adding an issue, say) that sends a shorter list and an unknown time as 00:00 keeps both
    again = save_minutes(store=STORE, viewer=SECRETARY, **{**MINUTES, "meeting_id": out["meeting_id"], "attendees": chair_only,
                                                        "start_time": "00:00", "transcript_id": None})
    assert again["saved"] and attendees(out["meeting_id"]) == ["김민수", "박지훈", "이서연"]
    with STORE.connect() as conn:
        starts = conn.execute("SELECT starts_at FROM meetings WHERE code = %s", (out["meeting_id"],)).fetchone()["starts_at"]
    assert starts.astimezone(KST).strftime("%H:%M") != "00:00"
    # Confirming without the lists keeps the stored decisions and Action Items
    confirm = {k: v for k, v in MINUTES.items() if k not in ("decisions", "action_items")}
    final = save_minutes(store=STORE, viewer=SECRETARY, **{**confirm, "meeting_id": out["meeting_id"], "finalize": True})
    assert final["finalized"], final.get("warnings")
    with STORE.connect() as conn:
        kept = conn.execute("SELECT (SELECT count(*) FROM decisions d WHERE d.meeting_id = m.id) AS d, "
                            "(SELECT count(*) FROM action_items a WHERE a.meeting_id = m.id) AS a FROM meetings m WHERE m.code = %s",
                            (out["meeting_id"],)).fetchone()
    assert (kept["d"], kept["a"]) == (1, 1)


def test_saving_the_same_recording_without_the_meeting_id_updates_that_meeting(meeting_db, monkeypatch):
    monkeypatch.setattr(meeting_minutes, "fetch_transcript", lambda ref: TRANSCRIPT)
    first = save_minutes(store=STORE, viewer=SECRETARY, **MINUTES)
    again = save_minutes(store=STORE, viewer=SECRETARY, **{**MINUTES, "summary": "고쳐 쓴 요약"})
    assert again["meeting_id"] == first["meeting_id"]
    with STORE.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM meetings WHERE title = %s", (MINUTES["title"],)).fetchone()["n"] == 1


def test_a_recording_of_a_day_with_one_booked_meeting_goes_to_that_meeting(meeting_db, monkeypatch):
    monkeypatch.setattr(meeting_minutes, "fetch_transcript", lambda ref: TRANSCRIPT)
    booked = meeting_schedule(store=STORE, viewer=SECRETARY, action="create", title="HBM 증설 재검토", starts_at="2026-11-03 19:00",
                              attendees=["김민수 (CEO)"], today=date(2026, 11, 3))
    out = save_minutes(store=STORE, viewer=SECRETARY, **MINUTES)  # date 2026-11-03, no meeting id
    assert out["meeting_id"] == booked["meeting"]["id"] and any("예약된 회의" in w for w in out["warnings"])
    with STORE.connect() as conn:
        row = conn.execute("SELECT status, starts_at FROM meetings WHERE code = %s", (out["meeting_id"],)).fetchone()
    assert row["status"] == "held" and row["starts_at"].astimezone(KST).strftime("%H:%M") == "19:00"


def test_without_a_chair_everyone_who_attended_hears_of_it(decided):
    with STORE.connect() as conn:
        conn.execute("UPDATE meeting_participants SET role = 'required'")
    assert scan([PRICE_CUT], Model({"effect": "at_risk", "reason": "단가 하락 신호"}))["mailed"] == 3


def test_one_article_shaking_two_premises_of_a_decision_is_one_mail_each(decided):
    with STORE.connect() as conn:
        conn.execute("UPDATE decision_premises SET watch = '{HBM}'")
    out = scan([PRICE_CUT], Model({"effect": "at_risk", "reason": "단가"}, {"effect": "at_risk", "reason": "수율"}))
    # 김민수 · 박지훈 once for the premise, then only 이서연 (who stated the condition) is new
    assert out == {"checked": 2, "shaken": 2, "mailed": 3}


def test_dismissing_a_signal_holds_the_premise_again(decided):
    scan([PRICE_CUT], Model({"effect": "at_risk", "reason": "단가 하락 신호"}))
    sid = dw.set_signal(store=STORE, viewer=SECRETARY)["signals"][0]["id"]
    staff = Viewer("staff@example.com", "최유진", frozenset({"staff"}), 3, True)
    assert dw.set_signal(store=STORE, viewer=staff, action="dismiss", signal_id=sid)["ok"] is False  # 확정 권한 없음
    assert dw.set_signal(store=STORE, viewer=SECRETARY, action="dismiss", signal_id=sid, note="일시적 할인") == {"ok": True, "signals": []}
    found = dw.review_decisions("HBM 증설", ["HBM"], store=STORE, viewer=SECRETARY)
    assert found["decisions"][0]["premises"][0]["status"] == "holding" and "signals" not in found["decisions"][0]["premises"][0]


def test_the_meeting_agent_has_the_review_tool():
    tool = next(t for t in tools_for("meeting") if t.name == review_tool.TOOL_NAME)
    assert set(tool.args) == {"query", "topics", "decision_ref"}


def test_meetings_of_one_matter_share_a_subject(meeting_db, monkeypatch):
    monkeypatch.setattr(meeting_minutes, "fetch_transcript", lambda ref: TRANSCRIPT)
    plain = {k: v for k, v in MINUTES.items() if k != "transcript_id"}
    kickoff = save_minutes(store=STORE, viewer=SECRETARY, **{**plain, "title": "신공장 추진 착수 회의", "date": "2026-10-06"})
    review = save_minutes(store=STORE, viewer=SECRETARY, **{**plain, "title": "투자심의위원회 (천안신공장 투자건)", "date": "2026-10-29"})
    other = save_minutes(store=STORE, viewer=SECRETARY, **{**plain, "title": "공급망 점검 회의", "date": "2026-10-30"})
    named = save_minutes(store=STORE, viewer=SECRETARY, **{**plain, "title": "협력사 간담회", "date": "2026-10-31", "subject": "공급망"})
    booked = meeting_schedule(store=STORE, viewer=SECRETARY, action="create", title="재검토 회의", starts_at="2026-11-10 14:00",
                              attendees=["김민수 (CEO)"], carry_over_from=review["meeting_id"], today=date(2026, 11, 3))
    with STORE.connect() as conn:
        subjects = {r["code"]: r["subject"] for r in conn.execute("SELECT code, subject FROM meetings")}
    assert subjects[kickoff["meeting_id"]] == subjects[review["meeting_id"]] == subjects[booked["meeting"]["id"]] == "신공장"
    assert subjects[other["meeting_id"]] == subjects[named["meeting_id"]] == "공급망"


def test_the_control_wall_shows_each_subjects_state_and_what_the_agent_did(decided):
    wall = control(store=STORE, viewer=SECRETARY, today=date(2026, 11, 3))
    [screen] = [s for s in wall["screens"] if s["subject"] == "HBM 증설"]  # the seeded meetings have no subject
    assert screen["subject"] == "HBM 증설" and screen["state"] == "ok" and screen["decision"]["ref"] == f"{decided}-D1"
    assert screen["counts"] == {"meetings": 1, "decisions": 1, "open_items": 1, "overdue": 0}
    scan([PRICE_CUT], Model({"effect": "broken", "reason": "18% 인하"}))
    later = control(store=STORE, viewer=SECRETARY, today=date(2026, 11, 21))  # the Action Item (due 11/20) is late by then
    screen = later["screens"][0]  # the alarm first
    assert screen["subject"] == "HBM 증설" and screen["state"] == "alarm" and later["counts"]["alarm"] == 1
    assert screen["reasons"][0].startswith("전제가 깨짐 — HBM 단가가 내년 말까지 현재 수준 유지 ← 테크데일리 (가상)")
    assert any(r.startswith("기한 지남 1건 — 세부 투자 계획 제출") for r in screen["reasons"])
    kinds = [e["kind"] for e in later["log"]]
    assert {"recording", "final", "signal", "alert"} <= set(kinds) and kinds.index("signal") < kinds.index("final")  # newest first


def test_a_replaced_decision_no_longer_lights_its_broken_premise(decided):
    scan([PRICE_CUT], Model({"effect": "broken", "reason": "18% 인하"}))
    assert control(store=STORE, viewer=SECRETARY, today=date(2026, 11, 3))["screens"][0]["state"] == "alarm"
    plain = {k: v for k, v in MINUTES.items() if k != "transcript_id"}
    save_minutes(store=STORE, viewer=SECRETARY, **{**plain, "title": "HBM 증설 재검토", "date": "2026-11-10", "finalize": True,
                                                    "decisions": [{"kind": "decision", "statement": "1단계만 6천억 원으로 증설",
                                                                   "supersedes": f"{decided}-D1"}]})
    wall = control(store=STORE, viewer=SECRETARY, today=date(2026, 11, 10))
    [screen] = [s for s in wall["screens"] if s["subject"] == "HBM 증설"]
    assert screen["state"] == "ok" and screen["decision"]["statement"] == "1단계만 6천억 원으로 증설"
    assert screen["decision"]["replaces"] == f"{decided}-D1" and any(e["kind"] == "change" for e in wall["log"])
    with STORE.connect() as conn:
        assert dw.open_signals(conn, SECRETARY) == []


def test_the_control_room_lists_meetings_and_a_draft_is_confirmed_from_it(decided, monkeypatch):
    from app.library.workflows import meeting_desk
    booked = meeting_schedule(store=STORE, viewer=SECRETARY, action="create", title="HBM 증설 재검토", starts_at="2026-11-10 14:00",
                              attendees=["김민수 (CEO)"], today=date(2026, 11, 3))
    rows = {r["id"]: r for r in control(store=STORE, viewer=SECRETARY, today=date(2026, 11, 3))["meetings"]}
    done, upcoming = rows[decided], rows[booked["meeting"]["id"]]
    assert done["phase"] == "done" and done["recording"]["name"] == "HBM_투심위.wav" and done["decisions"] == 1
    assert done["items"] == {"total": 1, "done": 0, "overdue": 0} and upcoming["phase"] == "upcoming"
    with STORE.connect() as conn:  # booked for an earlier day and still no minutes: not "in progress"
        conn.execute("UPDATE meetings SET starts_at = '2026-11-01 01:00+00', ends_at = NULL WHERE code = %s", (booked["meeting"]["id"],))
    late = {r["id"]: r for r in control(store=STORE, viewer=SECRETARY, today=date(2026, 11, 3))["meetings"]}[booked["meeting"]["id"]]
    assert late["phase"] == "pending"
    view = meeting_desk.meeting(store=STORE, viewer=SECRETARY, meeting_id=decided)
    assert view["recordings"][0]["lines"] == 3 and view["items"][0]["text"] == "세부 투자 계획 제출" and view["mails"]
    plain = {k: v for k, v in MINUTES.items() if k != "transcript_id"}
    draft = save_minutes(store=STORE, viewer=SECRETARY, **{**plain, "title": "HBM 협력사 회의", "date": "2026-11-04"})
    out = meeting_desk.finalize(store=STORE, viewer=SECRETARY, meeting_id=draft["meeting_id"])
    assert out["finalized"]
    with STORE.connect() as conn:
        kept = conn.execute("SELECT count(*) AS n FROM decisions d JOIN meetings m ON m.id = d.meeting_id WHERE m.code = %s",
                            (draft["meeting_id"],)).fetchone()["n"]
    assert kept == 1 and meeting_desk.finalize(store=STORE, viewer=SECRETARY, meeting_id=draft["meeting_id"])["ok"] is False


def test_meetings_from_before_subjects_get_one_from_their_titles(meeting_db):
    for title, day in [("캡스톤 팀 회의", "2026-10-13"), ("캡스톤 중간 발표 준비 회의", "2026-10-20"), ("브라이트셀 인수 1차 검토회의", "2026-10-30")]:
        save_minutes(store=STORE, viewer=SECRETARY, title=title, date=day, summary="-", attendees=[{"name": "이서연"}],
                     decisions=[], action_items=[])
    with STORE.connect() as conn:
        conn.execute("UPDATE meetings SET subject = NULL")  # saved before migration 007
    subjects = {m["title"]: m["subject"] for m in control(store=STORE, viewer=SECRETARY, today=date(2026, 11, 3))["meetings"]}
    assert [subjects[t] for t in ("캡스톤 팀 회의", "캡스톤 중간 발표 준비 회의", "브라이트셀 인수 1차 검토회의")] == ["캡스톤", "캡스톤", "브라이트셀"]
    assert subjects["그룹 투자심의위원회 사전회의"] == "그룹 투자심의위원회 사전회의"  # nothing but meeting words: the title


def test_the_control_room_shows_progress_by_owner(decided):
    rows = control(store=STORE, viewer=SECRETARY, today=date(2026, 11, 21))["people"]  # the item (due 11/20) is late by then
    [owner] = [r for r in rows if r["name"] == "이서연"]
    assert (owner["total"], owner["done"], owner["overdue"]) == (1, 0, 1) and owner["subjects"] == ["HBM 증설"]
    assert owner["items"][0]["meeting"]["id"] == decided and owner["items"][0]["overdue"] is True
    assert rows[0]["overdue"] >= rows[-1]["overdue"]  # late work first


def test_the_watch_reads_the_system_news_store(meeting_db, news_db, monkeypatch):
    """The watch job's articles come from the news store (the data file's articles and whatever the feeds brought),
    in the shape it judges: names from the stored mentions and analyses."""
    from app.systems.group.tools.run_decision_review import DECISION_WATCH

    monkeypatch.setattr(outbox, "lookup_email", lambda name: None)
    articles = DECISION_WATCH.articles()
    contract = next(a for a in articles if a["id"] == "NEWS-G-2026-09-22-01")
    assert contract["date"] == "2026-09-22" and "브라이트셀" in contract["companies"] and contract["headline"].startswith("브라이트셀")
    out = save_minutes(store=STORE, viewer=SECRETARY, title="브라이트셀 지분 투자 검토", date="2026-09-20", summary="투자 한도 승인",
                       attendees=[{"name": "정전략", "title": "그룹 전략실장", "role": "chair"}], finalize=True,
                       decisions=[{"kind": "decision", "statement": "브라이트셀 지분 투자 3천억 원 한도 승인",
                                   "premises": [{"kind": "premise", "statement": "최대 고객 공급계약이 유지된다",
                                                 "watch": ["브라이트셀"], "speaker": "정전략 (그룹 전략실장)"}]}])
    assert out.get("finalized"), out.get("warnings")
    with STORE.connect() as conn:
        found = dw.scan_articles(conn, articles, Model({"effect": "at_risk", "reason": "2027년 만료, 재계약 조건 미확정"}))
        signal = conn.execute("SELECT source_ref, effect FROM premise_signals").fetchone()
    assert found["shaken"] == 1 and (signal["source_ref"], signal["effect"]) == ("NEWS-G-2026-09-22-01", "at_risk")


def test_the_watch_reads_the_data_file_without_a_news_store():
    from app.systems.group import DATA_DIR

    watch = review_tool.make_decision_watch(STORE, DATA_DIR / "news.json", news=None)
    assert {a["id"] for a in watch.articles()} >= {"NEWS-G-2026-09-22-01"}
