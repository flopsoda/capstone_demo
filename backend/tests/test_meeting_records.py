"""Meeting records workflows over a real (test) Postgres: minutes draft → final → correction,
Action Item tracking and updates, the schedule, and who may see or change what."""

from datetime import date

import pytest

from app.library.workflows.action_items import track_action_items, update_action_item
from app.library.workflows.meeting_context_retrieval import retrieve_meeting_context
from app.library.workflows.meeting_minutes import save_minutes
from app.library.workflows.meeting_schedule import meeting_schedule
from app.library.workflows.meeting_store import Viewer, viewer_for
from app.runtime.events import collect_events, new_run, stream_run
from app.runtime.schemas import Principal
from app.systems.group import MEETING_STORE as GROUP_STORE
from app.systems.k_electronics import MEETING_STORE as STORE
from app.systems.k_electronics import registry as k_registry

pytestmark = pytest.mark.usefixtures("meeting_db")

TODAY = date(2026, 10, 25)
SECRETARY = Viewer("secretary@example.com", "이서연", frozenset({"secretary"}), 3, True)
STAFF = Viewer("staff@example.com", "박지훈", frozenset({"staff"}), 2, True)
A2A_EXEC = Viewer("ceo@example.com", "김민수", frozenset({"executive"}), 4, False)

DRAFT = dict(
    title="뉴로엣지 협상 전략회의",
    date="2026-10-24",
    summary="뉴로엣지 인수 가격 상한을 2.3조 원으로 낮추고 고객 집중도 실사를 확대하기로 했다.",
    classification=3,
    topics=["NeuroEdge", "뉴로엣지", "M&A"],
    attendees=[
        {"name": "김민수", "title": "CEO", "role": "chair"},
        {"name": "박지훈 (CFO)"},
        {"name": "이서연", "title": "CTO", "attended": False},
        {"name": "외부 자문위원", "role": "observer"},
    ],
    decisions=[
        {"kind": "criterion", "speaker": "박지훈 (CFO)", "statement": "가격 상한 2.3조 원", "rationale": "실사 결과 고객 집중도 38%",
         "supersedes": "MTG-2026-09-15-D1"},
        {"kind": "opinion", "statement": "고객 집중도가 높으면 인수 후 매출 변동성이 크다"},  # no speaker
        {"kind": "issue", "statement": "최대 고객사와의 장기 계약 갱신 여부"},
    ],
    action_items=[
        {"text": "최대 고객사 계약서 검토", "owner": "법무팀", "due": "2026-11-07"},
        {"text": "핵심 설계팀 Retention 패키지 설계"},  # owner and due not decided in the meeting
    ],
)


def save(viewer=SECRETARY, **overrides):
    return save_minutes(store=STORE, viewer=viewer, **{**DRAFT, **overrides})


def test_draft_then_finalize_needs_owner_and_due():
    out = save()
    assert out["saved"] and out["status"] == "draft" and out["meeting_id"] == "MTG-2026-10-24-01"
    rec = out["record"]
    assert [a["id"] for a in rec["action_items"]] == ["MTG-2026-10-24-01-A1", "MTG-2026-10-24-01-A2"]
    assert rec["decisions"][0]["supersedes"] == "MTG-2026-09-15-D1"
    assert rec["decisions"][1]["speaker"] == "발언자 확인 필요"
    assert any("A2" in w and "담당자 없음" in w for w in out["warnings"])
    assert {"사람: 김민수 (CEO)", "사람: 외부 자문위원"} <= set(out["new_directory_entries"])
    assert {(a["name"], a["role"], a["attended"]) for a in rec["attendees"]} >= {("이서연 (CTO)", "required", False), ("외부 자문위원", "observer", True)}

    # Asked to finalize while A2 has no owner/due: saved, but stays a draft.
    out = save(meeting_id="MTG-2026-10-24-01", finalize=True)
    assert out["status"] == "draft" and out["finalized"] is False
    # Drafts are not part of past-meeting retrieval.
    assert "MTG-2026-10-24-01" not in [m["id"] for m in retrieve_meeting_context("뉴로엣지", ["NeuroEdge"], store=STORE, viewer=SECRETARY)["meetings"]]

    items = [{**DRAFT["action_items"][0], "id": "MTG-2026-10-24-01-A1"},
             {"text": "핵심 설계팀 Retention 패키지 설계", "owner": "인사팀", "due": "2026-11-14", "id": "MTG-2026-10-24-01-A2"}]
    out = save(meeting_id="MTG-2026-10-24-01", action_items=items, finalize=True)
    assert out["status"] == "final" and out["finalized"] is True and out["record"]["finalized_by"] == "secretary@example.com"
    found = retrieve_meeting_context("뉴로엣지 가격 상한", ["NeuroEdge"], store=STORE, viewer=SECRETARY, today=TODAY)
    assert "MTG-2026-10-24-01" in [m["id"] for m in found["meetings"]]


def test_final_minutes_are_corrected_with_a_reason_and_keep_history():
    out = save(action_items=[{**a, "owner": a.get("owner") or "인사팀", "due": a.get("due") or "2026-11-14"} for a in DRAFT["action_items"]],
               finalize=True)
    code = out["meeting_id"]
    assert out["status"] == "final"
    refused = save(meeting_id=code, summary="바뀐 요약")
    assert refused["saved"] is False and "정정 사유" in refused["error"]

    out = save(meeting_id=code, summary="가격 상한 2.3조 원 (정정)", action_items=[], correction_reason="CFO 발언 수치 정정")
    assert out["saved"] and out["corrected"] and out["status"] == "final" and out["version"] == 2
    # A correction never deletes Action Items.
    assert len(out["record"]["action_items"]) == 2
    with STORE.connect() as conn:
        rev = conn.execute("SELECT version, reason, snapshot FROM minutes_revisions").fetchone()
    assert rev["version"] == 1 and rev["reason"] == "CFO 발언 수치 정정" and rev["snapshot"]["summary"] == DRAFT["summary"]


def test_writes_are_checked_against_the_requester():
    assert save(viewer=STAFF)["error"].startswith("요청자의 열람 등급(2)")  # cannot file a 대외비 meeting
    assert "다른 Agent System" in save(viewer=A2A_EXEC)["error"]
    assert save(viewer=SECRETARY, classification=4)["saved"] is False
    # A meeting the user cannot see cannot be written onto either.
    assert save(viewer=STAFF, classification=2, meeting_id="MTG-2026-09-15")["error"].startswith("회의 'MTG-2026-09-15'를 찾을 수 없거나")


def test_tracking_overdue_first_with_counts_and_scope():
    out = track_action_items(store=STORE, viewer=SECRETARY, today=TODAY)
    assert out["as_of"] == "2026-10-25 (일)"
    ids = [i["id"] for i in out["items"]]
    assert ids[:2] == ["MTG-2026-09-15-A1", "MTG-2026-07-08-A2"]  # 15 and 5 days overdue
    assert out["items"][0]["overdue_days"] == 15 and out["summary"]["overdue"] == 2 and out["summary"]["done"] == 3
    # A 사내 user does not see the 대외비 meeting's items at all.
    assert all(i["meeting"]["id"] != "MTG-2026-09-15" for i in track_action_items(store=STORE, viewer=STAFF, today=TODAY)["items"])
    # By owner, by due window, by text.
    assert [i["id"] for i in track_action_items(store=STORE, viewer=SECRETARY, owner="재무기획", today=TODAY)["items"]] == ["MTG-2026-09-15-A1"]
    soon = track_action_items(store=STORE, viewer=SECRETARY, due_within_days=7, today=TODAY)["items"]
    assert [i["id"] for i in soon] == ["MTG-2026-09-15-A1", "MTG-2026-07-08-A2", "MTG-2026-09-15-A3"]  # overdue + due 10-31
    assert [i["id"] for i in track_action_items(store=STORE, viewer=SECRETARY, query="기업가치", today=TODAY)["items"]] == ["MTG-2026-09-15-A1"]


def test_update_records_who_changed_what():
    out = update_action_item(store=STORE, viewer=SECRETARY, item_id="mtg-2026-09-15-a1", status="in_progress",
                             note="DCF 1차 산정 완료", due="2026-11-05", today=TODAY)
    assert out["updated"] and out["item"]["status"] == "in_progress" and out["item"]["due"] == "2026-11-05"
    h = out["history"][0]
    assert h["by"] == "secretary@example.com" and h["status"] == "open → in_progress" and "기한 2026-10-10 → 2026-11-05" in h["note"]
    done = update_action_item(store=STORE, viewer=SECRETARY, item_id="MTG-2026-09-15-A1", status="done", today=TODAY)
    assert done["item"]["status"] == "done" and len(done["history"]) == 2
    missing = update_action_item(store=STORE, viewer=SECRETARY, item_id="기업가치 산정", status="done")
    assert missing["updated"] is False and missing["candidates"][0]["id"] == "MTG-2026-09-15-A1"
    assert update_action_item(store=STORE, viewer=STAFF, item_id="MTG-2026-09-15-A2", status="done")["updated"] is False  # above clearance
    assert "다른 Agent System" in update_action_item(store=STORE, viewer=A2A_EXEC, item_id="MTG-2026-08-27-A1", status="done")["error"]


def test_schedule_create_with_carry_over_then_list_and_cancel():
    save(action_items=[])  # draft with one open issue
    out = meeting_schedule(store=STORE, viewer=SECRETARY, action="create", title="뉴로엣지 협상 후속회의",
                           starts_at="2026-11-03 14:00", attendees=["김민수", "박지훈"], agenda=["협상안 보고"],
                           classification=3, carry_over_from="MTG-2026-10-24-01", today=TODAY)
    assert out["ok"], out
    m = out["meeting"]
    assert m["id"] == "MTG-2026-11-03-01" and m["starts_at"] == "2026-11-03 14:00" and m["status"] == "scheduled"
    assert [a["title"] for a in m["agenda"]] == ["최대 고객사와의 장기 계약 갱신 여부", "협상안 보고"]
    listed = meeting_schedule(store=STORE, viewer=SECRETARY, today=TODAY)
    assert listed["today"] == "2026-10-25 (일)" and [x["id"] for x in listed["meetings"]] == ["MTG-2026-11-03-01"]
    assert meeting_schedule(store=STORE, viewer=STAFF, today=TODAY)["matched"] == 0  # 대외비
    assert meeting_schedule(store=STORE, viewer=SECRETARY, action="create", title="x", starts_at="2026-10-01 10:00", today=TODAY)["ok"] is False
    cancelled = meeting_schedule(store=STORE, viewer=SECRETARY, action="cancel", meeting_id="MTG-2026-11-03-01", today=TODAY)
    assert cancelled["meeting"]["status"] == "cancelled"
    # Minutes of a scheduled meeting go onto the same id.
    meeting_schedule(store=STORE, viewer=SECRETARY, action="create", title="정기 점검", starts_at="2026-11-04 10:00", today=TODAY)
    out = save(meeting_id="MTG-2026-11-04-01", title="정기 점검", date="2026-11-04", decisions=[], action_items=[])
    assert out["saved"] and out["meeting_id"] == "MTG-2026-11-04-01"


def test_carry_over_takes_open_issues_of_a_held_meeting_not_its_whole_agenda():
    meeting_schedule(store=STORE, viewer=SECRETARY, action="create", title="1차 검토", starts_at="2026-10-26 14:00",
                     agenda=["가격 범위", "실사 착수"], today=TODAY)
    save(meeting_id="MTG-2026-10-26-01", title="1차 검토", date="2026-10-26", action_items=[])  # one open issue
    held = meeting_schedule(store=STORE, viewer=SECRETARY, action="create", title="2차 검토", starts_at="2026-11-02 10:00",
                            carry_over_from="MTG-2026-10-26-01", today=TODAY)
    assert [a["title"] for a in held["meeting"]["agenda"]] == ["최대 고객사와의 장기 계약 갱신 여부"]
    # A meeting that never took place passes on everything it had planned.
    meeting_schedule(store=STORE, viewer=SECRETARY, action="cancel", meeting_id="MTG-2026-11-02-01", today=TODAY)
    again = meeting_schedule(store=STORE, viewer=SECRETARY, action="create", title="2차 검토 (재)", starts_at="2026-11-04 10:00",
                             carry_over_from="MTG-2026-11-02-01", today=TODAY)
    assert [a["title"] for a in again["meeting"]["agenda"]] == ["최대 고객사와의 장기 계약 갱신 여부"]


def test_schedule_attendee_labels_reuse_people_and_set_roles():
    save()  # registers 김민수 (CEO), 박지훈 (CFO), 이서연 (CTO)
    out = meeting_schedule(store=STORE, viewer=SECRETARY, action="create", title="인수 검토회의", starts_at="2026-11-03 14:00",
                           attendees=["김민수 (의장)", "박지훈 (K전자 CFO, 간사)", "이서연 이사"], today=TODAY)
    assert out["ok"], out
    assert out["new_directory_entries"] == []  # role words, a longer title and a courtesy suffix are the same people
    roles = {p["name"]: p["role"] for p in out["meeting"]["participants"]}
    assert roles == {"김민수 (CEO)": "chair", "박지훈 (CFO)": "secretary", "이서연 (CTO)": "required"}


def test_viewer_comes_from_the_run_not_from_the_model():
    assert viewer_for(None, "direct").clearance == 4  # tests: anonymous clearance 4
    p = Principal(email="staff@example.com", name="박지훈", groups=("staff",), clearance=2)
    assert viewer_for(p, "a2a").can_write is False and viewer_for(p, "direct").clearance == 2
    tool = k_registry.get_capability("run_action_item_tracking").impl
    run = new_run(system="k_electronics", root_agent="meeting", principal=p)
    events = list(stream_run(lambda: tool.invoke({}), run=run, hub=None))
    assert events[-1]["type"] == "done"
    # The tool ran as the 사내 user: the 대외비 meeting's items were filtered out before reading.
    out = tool.invoke({})  # outside a run: anonymous (clearance 4 here) sees them
    assert any(i["meeting"]["id"] == "MTG-2026-09-15" for i in out["items"])
    seen = []
    run2 = new_run(system="k_electronics", root_agent="meeting", principal=p)
    list(stream_run(lambda: seen.append(tool.invoke({})), run=run2, hub=None))
    assert seen and all(i["meeting"]["id"] != "MTG-2026-09-15" for i in seen[0]["items"])
    assert all("principal" not in e for e in events)  # never stamped on events


def test_each_system_has_its_own_store():
    g = retrieve_meeting_context("그룹 투자심의 사전회의", ["투자심의"], store=GROUP_STORE, viewer=SECRETARY)
    k = retrieve_meeting_context("그룹 투자심의 사전회의", ["투자심의"], store=STORE, viewer=SECRETARY)
    assert g["meetings"][0]["title"] == "그룹 투자심의위원회 사전회의"
    assert "그룹 투자심의위원회 사전회의" not in [m["title"] for m in k["meetings"]]


def test_tools_report_a_missing_store_as_a_result(monkeypatch):
    monkeypatch.delenv("MEETINGS_DATABASE_URL")
    STORE.reset()
    tool = k_registry.get_capability("run_meeting_context_retrieval").impl
    assert "MEETINGS_DATABASE_URL" in tool.invoke({"query": "뉴로엣지"})["error"]
    _, events = collect_events(lambda: tool.invoke({"query": "뉴로엣지"}))
    assert events == []
