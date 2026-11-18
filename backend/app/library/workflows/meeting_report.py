"""meeting_report — the minutes as two PDF documents: 회의록 (form) and 회의 결과 보고서 (report).

Layout follows what Korean public bodies publish (e.g. 공공데이터전략위원회 on data.go.kr): the 회의록 is a title, a
table with the item names on the left (회의일시 · 장소 · 참석 · 불참 · 안건) and the meeting content below; the
결과 보고 is written 개조식 (□ ○ -) and ends with 붙임 and "끝.". Both are built from the stored minutes only —
nothing here is written by the model. The desk downloads them; the outbox attaches them to minutes mail.
Not agent-visible.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psycopg

from .compliance import flags_of
from .meeting_minutes import minutes_record
from .meeting_store import KST, VISIBLE, MeetingStore, Viewer

KINDS = {"minutes": "회의록", "report": "회의 결과 보고서"}
REPORT_ROLES = ("chair", "secretary")  # who also gets the 결과 보고서 with the minutes mail
CLASSIFICATION = {1: "공개", 2: "사내", 3: "대외비", 4: "극비"}
DECIDED = {"decision": "결정", "resolution": "의결", "criterion": "기준"}
OUTCOME = {"approved": "원안가결", "amended": "수정가결", "rejected": "부결", "deferred": "보류"}
POSITION = {"against": "반대", "abstain": "기권", "recused": "회피"}
ITEM_STATUS = {"open": "미착수", "in_progress": "진행 중", "blocked": "막힘", "done": "완료", "cancelled": "취소"}
FONT_DIR = Path(os.environ.get("REPORT_FONT_DIR", "/usr/share/fonts/truetype/nanum"))
FONTS = {"myeongjo": ("NanumMyeongjo.ttf", "NanumMyeongjoBold.ttf"), "gothic": ("NanumGothic.ttf", "NanumGothicBold.ttf")}
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮"
LINE = 6.4  # mm, 10.5pt 명조 at about 160% (한글 default)


def fonts_available() -> bool:
    return all((FONT_DIR / f).is_file() for pair in FONTS.values() for f in pair)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def report_data(conn: psycopg.Connection, meeting_id: int) -> dict[str, Any]:
    """Everything the two documents show: the minutes record plus times, agenda, Compliance reviews and the
    meeting the open issues were carried over to."""
    data = minutes_record(conn, meeting_id)
    m = conn.execute(
        "SELECT m.code, m.starts_at, m.ends_at, m.classification, mi.drafted_by FROM meetings m JOIN minutes mi ON mi.meeting_id = m.id WHERE m.id = %s",
        (meeting_id,),
    ).fetchone()
    data["starts_at"], data["ends_at"], data["drafted_by"] = m["starts_at"], m["ends_at"], m["drafted_by"]
    data["agenda"] = [r["title"] for r in conn.execute(
        "SELECT title FROM agenda_items WHERE meeting_id = %s ORDER BY seq", (meeting_id,))]
    data["flags"] = flags_of(conn, meeting_id)
    nxt = conn.execute(
        """SELECT m2.code, m2.title, m2.starts_at, count(*) AS carried FROM agenda_items a JOIN meetings m2 ON m2.id = a.meeting_id
           WHERE m2.status = 'scheduled' AND m2.id <> %s AND m2.classification <= %s
             AND (a.carried_from IN (SELECT id FROM agenda_items WHERE meeting_id = %s) OR a.description LIKE %s)
           GROUP BY m2.id ORDER BY m2.starts_at LIMIT 1""",
        (meeting_id, m["classification"], meeting_id, f"이월 쟁점 {m['code']}-%"),
    ).fetchone()
    data["next_meeting"] = dict(nxt) if nxt else None
    return data


def filename(data: dict[str, Any], kind: str) -> str:
    draft = "" if data["status"] == "final" else "(초안)"
    return re.sub(r'[\\/:*?"<>|]+', " ", f"{data['date']}_{data['title']}_{KINDS[kind]}{draft}.pdf")


def content_disposition(name: str) -> str:
    return f"attachment; filename=\"meeting.pdf\"; filename*=UTF-8''{quote(name)}"


def meeting_pdf(*, store: MeetingStore, viewer: Viewer, meeting_id: str | None, kind: str | None) -> dict[str, Any]:
    """{filename, content} of one document of a meeting the viewer may read, or {error}."""
    if kind not in KINDS:
        return {"error": "kind는 minutes(회의록) 또는 report(회의 결과 보고서)입니다."}
    if not meeting_id:
        return {"error": "meeting_id가 필요합니다."}
    with store.connect() as conn:
        row = conn.execute(
            f"SELECT m.id FROM meetings m JOIN minutes mi ON mi.meeting_id = m.id WHERE m.code = %(code)s AND {VISIBLE}",
            {"code": meeting_id.strip(), **viewer.scope()},
        ).fetchone()
        if row is None:
            return {"error": "회의록이 없거나 열람 권한이 없습니다."}
        data = report_data(conn, row["id"])
    return {"filename": filename(data, kind), "content": render(data, kind)}


def render(data: dict[str, Any], kind: str) -> bytes:
    doc = _Document(data)
    (_minutes if kind == "minutes" else _report)(doc, data)
    return bytes(doc.output())


# ---------------------------------------------------------------------------
# Wording
# ---------------------------------------------------------------------------


def _day(value: date | datetime | str | None) -> str:
    """2026. 10. 30.(금)"""
    if value is None:
        return ""
    d = value.astimezone(KST).date() if isinstance(value, datetime) else date.fromisoformat(str(value)[:10])
    return f"{d.year}. {d.month}. {d.day}.({'월화수목금토일'[d.weekday()]})"


def _when(starts: datetime, ends: datetime | None) -> str:
    s = starts.astimezone(KST)
    text = f"{_day(s)} {s:%H:%M}"
    if ends:
        e = ends.astimezone(KST)
        text += f" ~ {e:%H:%M}" if e.date() == s.date() else f" ~ {_day(e)} {e:%H:%M}"
    return text


def _plain(label: str | None) -> str:
    """"박지훈 (CFO)" → "박지훈(CFO)", the way Korean documents write a name with its title."""
    return re.sub(r"\s+\(", "(", label or "").strip()


def _name_only(label: str | None) -> str:
    return re.sub(r"\s*\(.*\)\s*$", "", label or "").strip()


def _short_id(item_id: str) -> str:
    return item_id.rsplit("-", 1)[-1]


def _people(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    present = [a for a in data["attendees"] if a["attended"] is not False]
    return present, [a for a in data["attendees"] if a["attended"] is False]


def _with_role(a: dict[str, Any]) -> str:
    role = {"chair": "의장", "secretary": "간사", "observer": "배석"}.get(a["role"])
    return _plain(a["name"]) + (f" [{role}]" if role else "")


def _decision_line(d: dict[str, Any], *, speaker: bool = True) -> tuple[str, list[str]]:
    text = d["statement"].rstrip()
    if speaker and d.get("speaker"):
        text += f" — {_plain(d['speaker'])}"
    subs = []
    if d["kind"] == "resolution" and d.get("outcome"):
        votes = [f"{label} {d[k]}" for label, k in (("찬성", "votes_for"), ("반대", "votes_against"), ("기권", "votes_abstain")) if d.get(k) is not None]
        subs.append(f"의결 결과: {OUTCOME.get(d['outcome'], d['outcome'])}" + (f" ({' · '.join(votes)})" if votes else ""))
    for x in d.get("dissents", []):
        subs.append(f"{POSITION.get(x['position'], x['position'])}: {_plain(x['name'])}" + (f" — {x['reason']}" if x.get("reason") else ""))
    if d.get("rationale"):
        subs.append(f"근거: {d['rationale']}")
    if d.get("supersedes"):
        subs.append(f"이전 결정 {d['supersedes']}을(를) 변경")
    return text, subs


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def _fpdf():
    from fpdf import FPDF  # imported here so the rest of the store works where the PDF library is missing

    return FPDF


class _Document:
    """A4 page with the classification on top of every page and the record id on the bottom."""

    def __init__(self, data: dict[str, Any]):
        from fpdf import FontFace
        from fpdf.enums import XPos, YPos

        self.XPos, self.YPos = XPos, YPos
        self.label_face = FontFace(family="gothic", emphasis="BOLD", fill_color=(242, 242, 242))
        data_ref = data

        class Pdf(_fpdf()):
            def header(pdf) -> None:
                label = CLASSIFICATION.get(data_ref["classification"], "")
                if data_ref["status"] != "final":
                    label += " · 초안 (확정 전)"
                pdf.set_font("gothic", "B", 9)
                pdf.set_text_color(*((170, 30, 30) if data_ref["classification"] >= 3 or data_ref["status"] != "final" else (110, 110, 110)))
                pdf.cell(0, 5, label, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_text_color(0, 0, 0)
                pdf.ln(3)

            def footer(pdf) -> None:
                pdf.set_y(-14)
                pdf.set_font("gothic", "", 8)
                pdf.set_text_color(120, 120, 120)
                pdf.cell(0, 5, f"{data_ref['id']} · v{data_ref['version']} · Navitech 회의지원 Agent", align="L")
                pdf.set_x(pdf.l_margin)
                pdf.cell(0, 5, f"- {pdf.page_no()} / {{nb}} -", align="R")
                pdf.set_text_color(0, 0, 0)

        logging.getLogger("fontTools").setLevel(logging.ERROR)  # "TSI* NOT subset" notices for every font
        pdf = Pdf(format="A4")
        for family, (regular, bold) in FONTS.items():
            pdf.add_font(family, "", str(FONT_DIR / regular))
            pdf.add_font(family, "B", str(FONT_DIR / bold))
        pdf.set_margins(20, 16, 20)
        pdf.set_auto_page_break(True, margin=20)
        pdf.set_title(data["title"])
        pdf.set_author("Navitech 회의지원 Agent")
        pdf.set_creator("Navitech 회의지원 Agent")
        pdf.add_page()
        self.pdf = pdf

    def output(self) -> bytearray:
        return self.pdf.output()

    def title(self, text: str, size: float = 18) -> None:
        self.pdf.set_font("gothic", "B", size)
        self.pdf.multi_cell(0, size * 0.55, text, align="C", new_x=self.XPos.LMARGIN, new_y=self.YPos.NEXT)

    def right(self, text: str, size: float = 9.5) -> None:
        self.pdf.set_font("myeongjo", "", size)
        self.pdf.set_text_color(70, 70, 70)
        self.pdf.cell(0, 5.5, text, align="R", new_x=self.XPos.LMARGIN, new_y=self.YPos.NEXT)
        self.pdf.set_text_color(0, 0, 0)

    def heading(self, text: str, size: float = 12) -> None:
        if self.pdf.will_page_break(22):  # a heading never sits alone at the foot of a page
            self.pdf.add_page()
        self.pdf.ln(3.5)
        self.pdf.set_font("gothic", "B", size)
        self.pdf.multi_cell(0, 7.5, text, new_x=self.XPos.LMARGIN, new_y=self.YPos.NEXT)
        self.pdf.ln(0.8)

    def para(self, text: str, indent: float = 4) -> None:
        self.pdf.set_font("myeongjo", "", 10.5)
        for line in (text or "").splitlines():
            if line.strip():
                self.pdf.set_x(self.pdf.l_margin + indent)
                self.pdf.multi_cell(self.pdf.epw - indent, LINE, line.strip(), new_x=self.XPos.LMARGIN, new_y=self.YPos.NEXT)

    def bullet(self, mark: str, text: str, indent: float = 4, bold_mark: bool = False) -> None:
        """A list line whose wrapped lines start under the text, not under the mark."""
        pdf = self.pdf
        pdf.set_font("gothic" if bold_mark else "myeongjo", "B" if bold_mark else "", 10.5)
        width = pdf.get_string_width(mark) + 2
        pdf.set_x(pdf.l_margin + indent)
        pdf.cell(width, LINE, mark)
        pdf.set_font("myeongjo", "", 10.5)
        pdf.multi_cell(pdf.epw - indent - width, LINE, text, new_x=self.XPos.LMARGIN, new_y=self.YPos.NEXT)

    def form(self, rows: list[tuple[str, str]], label_width: float = 30) -> None:
        """The 회의록 header table: item name on the left, content on the right."""
        pdf = self.pdf
        pdf.set_font("myeongjo", "", 10.5)
        with pdf.table(col_widths=(label_width, pdf.epw - label_width), text_align=("CENTER", "LEFT"), line_height=LINE,
                       first_row_as_headings=False, padding=(1.6, 2.4), v_align="MIDDLE") as table:
            for label, value in rows:
                row = table.row()
                row.cell(label, style=self.label_face)
                row.cell(value)

    def grid(self, headings: tuple[str, ...], rows: list[tuple[str, ...]], widths: tuple[float, ...],
             align: tuple[str, ...]) -> None:
        pdf = self.pdf
        pdf.set_font("myeongjo", "", 9.8)
        with pdf.table(col_widths=widths, text_align=align, line_height=5.8, headings_style=self.label_face,
                       padding=(1.4, 1.8), v_align="MIDDLE") as table:
            head = table.row()
            for h in headings:
                head.cell(h, align="CENTER")
            for values in rows:
                row = table.row()
                for v in values:
                    row.cell(v)

    def box(self, text: str) -> None:
        pdf = self.pdf
        pdf.set_font("myeongjo", "", 10.5)
        pdf.set_fill_color(246, 246, 246)
        pdf.set_draw_color(150, 150, 150)
        pdf.multi_cell(0, LINE, text, border=1, fill=True, padding=(2.6, 3.4), new_x=self.XPos.LMARGIN, new_y=self.YPos.NEXT)
        pdf.set_draw_color(0, 0, 0)

    def gap(self, mm: float = 2) -> None:
        self.pdf.ln(mm)


# ---------------------------------------------------------------------------
# 회의록 (form)
# ---------------------------------------------------------------------------


def _minutes(doc: _Document, data: dict[str, Any]) -> None:
    present, absent = _people(data)
    doc.title(f"{data['title']} 회의록")
    doc.gap(5)
    rows = [("회의일시", _when(data["starts_at"], data["ends_at"]))]
    if data.get("location"):
        rows.append(("장소", data["location"]))
    rows.append(("참석자", ", ".join(_with_role(a) for a in present) + f"  (총 {len(present)}명)" if present else "기록 없음"))
    rows.append(("불참자", ", ".join(_plain(a["name"]) for a in absent) + f"  ({len(absent)}명)" if absent else "없음"))
    if data["agenda"]:
        rows.append(("안건", "\n".join(f"{CIRCLED[i] if i < len(CIRCLED) else f'{i + 1}.'} {t}" for i, t in enumerate(data["agenda"]))))
    doc.form(rows)

    number = iter(range(1, 10))
    doc.heading(f"{next(number)}. 회의 요약")
    doc.para(data["summary"])

    if data.get("body"):
        doc.heading(f"{next(number)}. 논의 경과")
        doc.para(data["body"])
    opinions = [d for d in data["decisions"] if d["kind"] == "opinion"]
    if opinions:
        doc.heading(f"{next(number)}. 주요 발언")
        for d in opinions:
            doc.bullet("○", (f"{_plain(d['speaker'])}: " if d.get("speaker") else "") + d["statement"])

    decided = [d for d in data["decisions"] if d["kind"] in DECIDED]
    doc.heading(f"{next(number)}. 결정 사항")
    if not decided:
        doc.para("결정된 사항 없음")
    for d in decided:
        text, subs = _decision_line(d)
        doc.bullet("○", f"[{DECIDED[d['kind']]}] {text}")
        for s in subs:
            doc.bullet("-", s, indent=10)

    doc.heading(f"{next(number)}. 후속 조치")
    if data["action_items"]:
        doc.grid(("번호", "조치 내용", "담당", "기한", "상태"),
                 [(_short_id(a["id"]), a["text"], _plain(a["owner"]) or "미정", _day(a["due"]) or "미정", ITEM_STATUS.get(a["status"], a["status"]))
                  for a in data["action_items"]],
                 widths=(11, 70, 37, 33, 19), align=("CENTER", "LEFT", "LEFT", "CENTER", "CENTER"))
    else:
        doc.para("후속 조치 없음")

    issues = [d for d in data["decisions"] if d["kind"] == "issue"]
    if issues:
        doc.heading(f"{next(number)}. 미결 쟁점")
        for d in issues:
            doc.bullet("○", d["statement"])

    doc.gap(8)
    if data["status"] == "final" and data.get("finalized_at"):
        doc.right(f"확정  {data['finalized_by'] or ''}  {_day(data['finalized_at'])} {datetime.fromisoformat(data['finalized_at']).astimezone(KST):%H:%M}")
    else:
        doc.right("확정 전 초안")
    doc.right(f"작성  Navitech 회의지원 Agent" + (f" (요청 {data['drafted_by']})" if data.get("drafted_by") else ""))


# ---------------------------------------------------------------------------
# 회의 결과 보고서 (개조식)
# ---------------------------------------------------------------------------


def _report(doc: _Document, data: dict[str, Any]) -> None:
    present, absent = _people(data)
    chair = next((a for a in present if a["role"] == "chair"), None)
    secretary = next((a for a in data["attendees"] if a["role"] == "secretary"), None)
    doc.title(f"「{data['title']}」 결과 보고", size=17)
    doc.gap(2)
    written = _day(data["finalized_at"]) if data.get("finalized_at") else _day(date.today())
    doc.right(f"{written}" + (f"  ·  간사 {_plain(secretary['name'])}" if secretary else ""))
    doc.gap(3)
    doc.box(data["summary"])

    doc.heading("□ 회의 개요")
    doc.bullet("○", f"일    시 : {_when(data['starts_at'], data['ends_at'])}")
    if data.get("location"):
        doc.bullet("○", f"장    소 : {data['location']}")
    if chair:
        who = f"{_name_only(chair['name'])} 의장 외 {len(present) - 1}명 (총 {len(present)}명)"
    else:
        who = f"총 {len(present)}명"
    doc.bullet("○", f"참    석 : {who}")
    if absent:
        doc.bullet("-", f"불참 {len(absent)}명: {', '.join(_plain(a['name']) for a in absent)}", indent=10)
    if data["agenda"]:
        doc.bullet("○", f"안    건 : {len(data['agenda'])}건")
        for i, t in enumerate(data["agenda"]):
            doc.bullet(CIRCLED[i] if i < len(CIRCLED) else f"{i + 1}.", t, indent=10)

    decided = [d for d in data["decisions"] if d["kind"] in DECIDED]
    doc.heading("□ 주요 결과")
    if not decided:
        doc.bullet("○", "결정된 사항 없음")
    for d in decided:
        text, subs = _decision_line(d, speaker=False)
        doc.bullet("○", text)
        for s in subs:
            doc.bullet("-", s, indent=10)

    open_items = [a for a in data["action_items"] if a["status"] not in ("done", "cancelled")]
    if data["action_items"]:
        doc.heading("□ 후속 조치")
        done = len(data["action_items"]) - len(open_items)
        doc.bullet("○", f"총 {len(data['action_items'])}건" + (f" (완료 {done}건)" if done else "") + ", 기한 전 담당자에게 자동 알림")
        doc.gap(1)
        doc.grid(("번호", "과제", "담당", "기한"),
                 [(_short_id(a["id"]), a["text"] + (" (완료)" if a["status"] == "done" else ""), _plain(a["owner"]) or "미정", _day(a["due"]) or "미정")
                  for a in data["action_items"]],
                 widths=(11, 86, 40, 33), align=("CENTER", "LEFT", "LEFT", "CENTER"))

    issues = [d for d in data["decisions"] if d["kind"] == "issue"]
    nxt = data.get("next_meeting")
    if issues or nxt:
        doc.heading("□ 향후 계획")
        if issues:
            doc.bullet("○", "미결 쟁점")
            for d in issues:
                doc.bullet("-", d["statement"], indent=10)
        if nxt:
            doc.bullet("○", f"다음 회의 : {_when(nxt['starts_at'], None)} 「{nxt['title']}」 (이월 안건 {nxt['carried']}건)")

    flags = data["flags"]
    if flags:
        doc.heading("□ 준법(Compliance) 검토")
        counts = {s: sum(f["status"] == s for f in flags) for s in ("acknowledged", "cleared", "open")}
        rules = sorted({f["rule"] for f in flags})
        doc.bullet("○", f"법적 검토가 필요할 수 있는 내용 {len(flags)}건")
        doc.bullet("-", f"해당 규칙: {', '.join(rules)}", indent=10)
        result = [f"{label} {counts[s]}건" for s, label in (("acknowledged", "조치"), ("cleared", "문제없음"), ("open", "미검토")) if counts[s]]
        doc.bullet("-", "검토 결과: " + " · ".join(result), indent=10)
        for note in dict.fromkeys(f.get("review_note") for f in flags if f.get("review_note")):
            doc.bullet("-", f"조치 내용: {note}", indent=10)

    doc.gap(6)
    doc.bullet("붙임", f"「{data['title']}」 회의록(v{data['version']}) 1부.  끝.", indent=0, bold_mark=True)
