"""Meeting control (회의 관제) — the whole company's meetings on one wall, as the Meeting Agent watches them.

Like a building's camera room: one screen per subject (천안 신공장, 뉴로엣지 인수 …) with its state, and the log of
what the agent saw and did. Nothing here is stored anew; it is read from what the meetings already hold:
  state    이상 alarm — a premise of a decision in force is broken, or mail could not be sent
           주의 warn  — a premise shaken, Action Items past due, minutes waiting for confirmation, an address missing
           정상 ok
  screen   the decision in force (the latest, with what it replaced), its premises, open / late Action Items, the next
           meeting, and the subject's meetings in order (for zooming in)
  log      recordings turned into drafts, minutes confirmed, decisions changed, premises shaken by news, mail sent,
           Action Items whose status changed (and progress the owners reported from their mail)
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from .decision_watch import EFFECT_LABEL, WATCHED_KINDS
from .mail_actions import ITEM_LABEL, VIA
from .meeting_store import KST, PERSON_LABEL, VISIBLE, MeetingStore, Viewer, decision_ref, kst_date, kst_minute, time_label, today_kst
from .subjects import fill_missing

NO_SUBJECT = "주제 없음"
LOG_LIMIT = 40
STATE_ORDER = {"alarm": 0, "warn": 1, "ok": 2}
MAIL_LABEL = {"minutes": "회의록", "assignment": "업무 배정", "schedule": "회의 안내", "reminder": "기한 알림", "nudge": "업무 독촉",
              "alert": "전제 변화"}
OPEN_ITEM = ("open", "in_progress", "blocked")


def _short(text: str, n: int = 60) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def control(*, store: MeetingStore, viewer: Viewer, today: date | None = None) -> dict[str, Any]:
    today = today or today_kst()
    scope = viewer.scope()
    with store.connect() as conn:
        if viewer.can_write:
            fill_missing(conn, viewer)
        meetings = conn.execute(
            f"""SELECT m.id, m.code, m.title, coalesce(m.subject, %(none)s) AS subject, m.status, m.starts_at, m.ends_at,
                       m.created_at, mi.status AS minutes, mi.finalized_at
                FROM meetings m LEFT JOIN minutes mi ON mi.meeting_id = m.id
                WHERE {VISIBLE} AND m.status <> 'cancelled' ORDER BY m.starts_at""",
            {**scope, "none": NO_SUBJECT},
        ).fetchall()
        ids = [m["id"] for m in meetings]
        decisions = conn.execute(
            f"""SELECT d.id, d.meeting_id, d.seq, d.kind, d.statement, coalesce({PERSON_LABEL}, d.speaker_label) AS speaker,
                       (SELECT mo.code || '-D' || o.seq FROM decisions o JOIN meetings mo ON mo.id = o.meeting_id WHERE o.id = d.supersedes_id) AS replaces,
                       (SELECT n.id FROM decisions n WHERE n.supersedes_id = d.id LIMIT 1) AS replaced_by
                FROM decisions d LEFT JOIN people p ON p.id = d.speaker_id
                WHERE d.meeting_id = ANY(%(ids)s) AND d.kind = ANY(%(kinds)s) ORDER BY d.meeting_id, d.seq""",
            {"ids": ids, "kinds": list(WATCHED_KINDS)},
        ).fetchall()
        premises = conn.execute(
            """SELECT dp.decision_id, dp.kind, dp.statement, dp.status, dp.due_date FROM decision_premises dp
               WHERE dp.decision_id = ANY(%s) ORDER BY dp.decision_id, dp.seq""",
            ([d["id"] for d in decisions],),
        ).fetchall()
        signals = conn.execute(
            """SELECT s.id, s.effect, s.headline, s.source_name, s.source_date, s.created_at, s.status, dp.statement AS premise,
                      d.meeting_id, d.seq, EXISTS (SELECT 1 FROM decisions n WHERE n.supersedes_id = d.id) AS replaced
               FROM premise_signals s JOIN decision_premises dp ON dp.id = s.premise_id JOIN decisions d ON d.id = dp.decision_id
               WHERE d.meeting_id = ANY(%s) AND s.effect <> 'none' ORDER BY s.created_at""",
            (ids,),
        ).fetchall()
        items = conn.execute(
            f"""SELECT a.code, a.meeting_id, a.text, a.status, a.due_date, coalesce({PERSON_LABEL}, u.name) AS owner
                FROM action_items a LEFT JOIN people p ON p.id = a.owner_person_id LEFT JOIN org_units u ON u.id = a.owner_unit_id
                WHERE a.meeting_id = ANY(%(ids)s) AND a.status = ANY(%(open)s) ORDER BY a.due_date NULLS LAST""",
            {"ids": ids, "open": list(OPEN_ITEM)},
        ).fetchall()
        mails = conn.execute(
            """SELECT o.meeting_id, CASE WHEN o.dedupe_key LIKE 'nudge:%%' THEN 'nudge' ELSE o.kind END AS kind, o.status, o.to_name,
                      coalesce(o.sent_at, o.created_at) AS at FROM outbox o
               WHERE o.meeting_id = ANY(%s) ORDER BY at""",
            (ids,),
        ).fetchall()
        recordings = conn.execute(
            "SELECT t.meeting_id, t.source_name, t.duration_s, t.created_at FROM transcripts t WHERE t.meeting_id = ANY(%s)", (ids,),
        ).fetchall()
        reports = conn.execute(  # 할 일의 상태가 바뀐 때와 담당자가 메일로 알린 때 (누가: 그 이메일의 사람)
            """SELECT a.meeting_id, a.text, u.old_status, u.new_status, u.note, u.at,
                       coalesce((SELECT p.display_name FROM people p WHERE lower(p.email) = lower(u.by_email) LIMIT 1), u.by_email) AS who
                FROM action_item_updates u JOIN action_items a ON a.id = u.action_item_id
                WHERE a.meeting_id = ANY(%s) AND (u.new_status IS DISTINCT FROM u.old_status OR u.note LIKE %s) ORDER BY u.at""",
            (ids, f"{VIA}%"),
        ).fetchall()
        progress = {r["meeting_id"]: r for r in conn.execute(
            """SELECT a.meeting_id, count(*) AS total, count(*) FILTER (WHERE a.status = 'done') AS done,
                      count(*) FILTER (WHERE a.status = ANY(%(open)s) AND a.due_date < %(today)s) AS overdue
               FROM action_items a WHERE a.meeting_id = ANY(%(ids)s) AND a.status <> 'cancelled' GROUP BY a.meeting_id""",
            {"ids": ids, "open": list(OPEN_ITEM), "today": today},
        )}
        now = conn.execute("SELECT now() AS t").fetchone()["t"]
        owned = conn.execute(
            """SELECT a.code, a.text, a.status, a.due_date, a.meeting_id, a.owner_person_id, a.owner_unit_id,
                      p.display_name, p.title, p.email, (p.is_external OR coalesce(sd.is_external, false)) AS is_external,
                      u.name AS unit, sd.department, sd.organization, lu.note AS last_note, lu.at AS last_at, lu.who AS last_by
               FROM action_items a LEFT JOIN people p ON p.id = a.owner_person_id LEFT JOIN org_units u ON u.id = a.owner_unit_id
               LEFT JOIN LATERAL (SELECT s.department, s.organization, s.is_external FROM staff_directory s
                                  WHERE (p.email IS NOT NULL AND lower(s.email) = lower(p.email)) OR s.name = p.display_name
                                  ORDER BY (lower(s.email) = lower(coalesce(p.email, ''))) DESC LIMIT 1) sd ON true
               LEFT JOIN LATERAL (SELECT x.note, x.at, coalesce((SELECT q.display_name FROM people q WHERE lower(q.email) = lower(x.by_email)
                                                                 LIMIT 1), x.by_email) AS who
                                  FROM action_item_updates x WHERE x.action_item_id = a.id AND x.note IS NOT NULL
                                  ORDER BY x.at DESC, x.id DESC LIMIT 1) lu ON true
               WHERE a.meeting_id = ANY(%s) AND a.status <> 'cancelled' ORDER BY a.due_date NULLS LAST, a.id""",
            (ids,),
        ).fetchall()

    by_id = {m["id"]: m for m in meetings}
    subject_of = {m["id"]: m["subject"] for m in meetings}
    premises_of: dict[int, list] = defaultdict(list)
    for p in premises:
        premises_of[p["decision_id"]].append(p)

    screens: dict[str, dict[str, Any]] = {}
    for m in meetings:
        s = screens.setdefault(m["subject"], {"subject": m["subject"], "meetings": [], "decisions": [], "items": [], "signals": [],
                                              "drafts": 0, "unsent": 0, "next": None})
        s["meetings"].append({"id": m["code"], "title": m["title"], "date": kst_date(m["starts_at"]),
                              "time": time_label(m["starts_at"], m["ends_at"]),
                              "status": "scheduled" if m["status"] == "scheduled" else (m["minutes"] or "none")})
        if m["status"] == "scheduled" and kst_date(m["starts_at"]) >= today.isoformat() and s["next"] is None:
            s["next"] = {"id": m["code"], "title": m["title"], "starts_at": kst_minute(m["starts_at"])}
        if m["minutes"] == "draft":
            s["drafts"] += 1
    for d in decisions:
        m = by_id[d["meeting_id"]]
        if m["minutes"] != "final":
            continue
        screens[m["subject"]]["decisions"].append({
            "ref": decision_ref(m["code"], d["seq"]), "statement": d["statement"], "speaker": d["speaker"], "date": kst_date(m["starts_at"]),
            "replaces": d["replaces"], "in_force": d["replaced_by"] is None,
            "premises": [{"kind": p["kind"], "statement": p["statement"], "status": p["status"],
                          "due": p["due_date"].isoformat() if p["due_date"] else None} for p in premises_of[d["id"]]],
        })
    for it in items:
        due = it["due_date"]
        screens[subject_of[it["meeting_id"]]]["items"].append({
            "id": it["code"], "text": it["text"], "owner": it["owner"], "due": due.isoformat() if due else None,
            "overdue": bool(due and due < today)})
    for sg in signals:
        if sg["status"] == "open" and not sg["replaced"]:  # a decision since replaced no longer rests on that premise
            screens[subject_of[sg["meeting_id"]]]["signals"].append({
                "effect": sg["effect"], "premise": sg["premise"], "headline": sg["headline"], "source": sg["source_name"],
                "ref": decision_ref(by_id[sg["meeting_id"]]["code"], sg["seq"])})
    for ml in mails:
        if ml["status"] in ("failed", "needs_address"):
            screens[subject_of[ml["meeting_id"]]]["unsent"] += 1

    out = []
    for s in screens.values():
        in_force = [d for d in s["decisions"] if d["in_force"]]
        broken = [x for x in s["signals"] if x["effect"] == "broken"]
        shaken = [x for x in s["signals"] if x["effect"] == "at_risk"]
        late = [i for i in s["items"] if i["overdue"]]
        reasons = [f"{EFFECT_LABEL['broken']} — {_short(x['premise'], 34)} ← {x['source']} \"{_short(x['headline'], 30)}\"" for x in broken]
        reasons += [f"{EFFECT_LABEL['at_risk']} — {_short(x['premise'], 34)} ← {x['source']} \"{_short(x['headline'], 30)}\"" for x in shaken]
        if s["unsent"]:
            reasons.append(f"보내지 못한 메일 {s['unsent']}통")
        if late:
            reasons.append(f"기한 지남 {len(late)}건 — {_short(late[0]['text'], 30)} ({late[0]['owner'] or '담당 미정'})")
        if s["drafts"]:
            reasons.append(f"확정 전 회의록 {s['drafts']}건")
        state = "alarm" if broken or s["unsent"] else "warn" if shaken or late or s["drafts"] else "ok"
        out.append({
            "subject": s["subject"], "state": state, "reasons": reasons,
            "decision": in_force[-1] if in_force else None,
            "counts": {"meetings": sum(1 for m in s["meetings"] if m["status"] != "scheduled"), "decisions": len(in_force),
                       "open_items": len(s["items"]), "overdue": len(late)},
            "next": s["next"], "meetings": s["meetings"], "decisions": s["decisions"], "items": s["items"], "signals": s["signals"],
            "last": max((m["date"] for m in s["meetings"] if m["status"] != "scheduled"), default=None),
        })
    out.sort(key=lambda x: (STATE_ORDER[x["state"]], x["subject"] == NO_SUBJECT, -(int((x["last"] or "0").replace("-", "")))))

    # What the agent saw and did, newest first
    log: list[dict[str, Any]] = []

    def event(at, meeting_id, kind, text):
        if at is not None:
            log.append({"at": at, "subject": subject_of[meeting_id], "meeting_id": by_id[meeting_id]["code"], "kind": kind, "text": text})

    for r in recordings:
        event(r["created_at"], r["meeting_id"], "recording", f"녹음 → 회의록 초안 — {by_id[r['meeting_id']]['title']}")
    recorded = {r["meeting_id"] for r in recordings}
    for m in meetings:
        if m["minutes"] == "draft" and m["id"] not in recorded:
            event(m["created_at"], m["id"], "draft", f"회의록 초안 저장 — {m['title']} (확정 대기)")
    decided: dict[int, list] = defaultdict(list)
    for d in decisions:
        decided[d["meeting_id"]].append(d)
    for m in meetings:
        if m["minutes"] == "final" and m["finalized_at"]:
            n_items = sum(1 for it in items if it["meeting_id"] == m["id"])
            event(m["finalized_at"], m["id"], "final", f"회의록 확정 — {m['title']} ({kst_date(m['starts_at'])[5:].replace('-', '/')}) · 결정 {len(decided[m['id']])}")
            for d in decided[m["id"]]:
                if d["replaces"]:
                    event(m["finalized_at"], m["id"], "change", f"결정 변경 — {d['replaces']} → {_short(d['statement'], 46)}")
    for r in reports:
        by_mail = (r["note"] or "").startswith(VIA)
        reason = (r["note"] or "").removeprefix(VIA).lstrip(": ").strip() if by_mail else ""
        event(r["at"], r["meeting_id"], "report",
              f"진행 보고 — {r['who'] or '누군가'}: {ITEM_LABEL.get(r['new_status'], r['new_status'])} · {_short(r['text'], 30)}"
              + (" (메일)" if by_mail else "") + (f" — {_short(reason, 40)}" if reason else ""))
    for sg in signals:
        event(sg["created_at"], sg["meeting_id"], "signal",
              f"{EFFECT_LABEL[sg['effect']]} — {_short(sg['premise'], 34)} ← {sg['source_name']} \"{_short(sg['headline'], 34)}\"")
    sent: dict[tuple, list] = defaultdict(list)
    for ml in mails:
        if ml["status"] in ("sent", "approved", "pending"):
            sent[(ml["meeting_id"], ml["kind"], ml["at"].astimezone(KST).strftime("%Y-%m-%d %H:%M"))].append(ml)
    for (mid, kind, _), group in sent.items():
        names = ", ".join(dict.fromkeys(g["to_name"].split(" (")[0] for g in group))
        text = (f"알림 메일 {len(group)}통 — 전제 변화 ({names})" if kind == "alert"
                else f"회의 예약 · 안내 메일 {len(group)}통 — {by_id[mid]['title']}" if kind == "schedule"
                else f"메일 {len(group)}통 — {MAIL_LABEL.get(kind, kind)} ({names})")
        event(group[0]["at"], mid, "alert" if kind == "alert" else "mail", text)
    log.sort(key=lambda e: e["at"], reverse=True)
    for e in log:
        e["at"] = e["at"].astimezone(KST).strftime("%Y-%m-%d %H:%M")
    counts = {k: sum(1 for x in out if x["state"] == k) for k in STATE_ORDER}
    return {"today": today.isoformat(), "screens": out, "counts": counts, "log": log[:LOG_LIMIT],
            "meetings": _meeting_rows(meetings, decisions, recordings, progress, mails, now, today),
            "people": _owners(owned, by_id, today)}


def _owners(owned, by_id, today: date) -> list[dict[str, Any]]:
    """Action Items by who owns them — one person across meetings and subjects (the same e-mail, or the same name,
    is one person), a department that owns work as its own row; with where they belong (department · organization)."""
    people: dict[str, dict[str, Any]] = {}
    for a in owned:
        if a["owner_person_id"]:
            key = (a["email"] or a["display_name"]).lower()
            row = people.setdefault(key, {"name": a["display_name"], "title": a["title"], "department": a["department"],
                                          "organization": a["organization"], "external": bool(a["is_external"]),
                                          "unit": False, "items": []})
        elif a["owner_unit_id"]:
            row = people.setdefault(f"unit:{a['unit']}", {"name": a["unit"], "title": None, "department": a["unit"],
                                                          "organization": None, "external": False, "unit": True, "items": []})
        else:
            row = people.setdefault("none", {"name": "담당 미정", "title": None, "department": None, "organization": None,
                                             "external": False, "unit": False, "items": []})
        m = by_id[a["meeting_id"]]
        due = a["due_date"]
        row["items"].append({"id": a["code"], "text": a["text"], "status": a["status"], "due": due.isoformat() if due else None,
                             "overdue": bool(due and due < today and a["status"] in OPEN_ITEM), "subject": m["subject"],
                             "meeting": {"id": m["code"], "title": m["title"], "date": kst_date(m["starts_at"])},
                             # 마지막 기록 (담당자가 메일로 알린 메모, 독촉 · 기한 변경 등)
                             "last": {"note": a["last_note"], "at": kst_minute(a["last_at"]), "by": a["last_by"]} if a["last_note"] else None})
    out = []
    for row in people.values():
        items = row["items"]
        open_due = sorted(i["due"] for i in items if i["due"] and i["status"] in OPEN_ITEM)
        out.append({**row, "total": len(items), "done": sum(i["status"] == "done" for i in items),
                    "in_progress": sum(i["status"] == "in_progress" for i in items),
                    "overdue": sum(i["overdue"] for i in items), "next_due": open_due[0] if open_due else None,
                    "subjects": sorted({i["subject"] for i in items})})
    out.sort(key=lambda r: (-r["overdue"], r["done"] / r["total"] if r["total"] else 1, r["name"]))
    return out


def _meeting_rows(meetings, decisions, recordings, progress, mails, now, today: date) -> list[dict[str, Any]]:
    """Every meeting as a row: live (booked, begun today, no minutes yet) · pending (booked for an earlier day and still
    no minutes: the recording is yet to come) · upcoming · done (recording · minutes · decisions · Action Items done / all ·
    mail sent / all)."""
    decided: dict[int, int] = defaultdict(int)
    for d in decisions:
        decided[d["meeting_id"]] += 1
    recording = {r["meeting_id"]: r for r in recordings}
    sent: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for ml in mails:
        sent[ml["meeting_id"]][1] += 1
        sent[ml["meeting_id"]][0] += ml["status"] == "sent"
    rows = []
    for m in meetings:
        phase = ("done" if m["status"] == "held" else "upcoming" if m["starts_at"] > now
                 else "live" if kst_date(m["starts_at"]) == today.isoformat() else "pending")
        rec, prog = recording.get(m["id"]), progress.get(m["id"])
        rows.append({
            "id": m["code"], "title": m["title"], "subject": m["subject"], "phase": phase, "date": kst_date(m["starts_at"]),
            "time": time_label(m["starts_at"], m["ends_at"]), "starts_at": kst_minute(m["starts_at"]), "minutes": m["minutes"],
            "recording": {"name": rec["source_name"], "seconds": round(rec["duration_s"] or 0)} if rec else None,
            "decisions": decided[m["id"]], "items": {"total": prog["total"], "done": prog["done"], "overdue": prog["overdue"]} if prog else None,
            "mails": {"sent": sent[m["id"]][0], "total": sent[m["id"]][1]} if m["id"] in sent else None,
        })
    order = {"live": 0, "pending": 1, "upcoming": 2, "done": 3}
    rows.sort(key=lambda r: (order[r["phase"]], r["starts_at"] if r["phase"] != "done" else "", r["starts_at"]), reverse=False)
    done = sorted((r for r in rows if r["phase"] == "done"), key=lambda r: r["starts_at"], reverse=True)
    return [r for r in rows if r["phase"] != "done"] + done
