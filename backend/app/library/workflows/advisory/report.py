"""report — the 투자심의 보고서 PDF of a stored Advisory judgement (not agent-visible).

The document records a verified judgement; it does not judge. Every sentence of judgement is the stored opinion's
own text, every figure is the `display` (or a table value) of the evidence the judgement was stored with, and the
recommendation label is opinion.RECOMMENDATIONS. Nothing here computes a business figure: the only arithmetic is
layout (bar heights, axis positions, order). Sections whose data the company file does not have are left out, so a
company with less data gets a shorter report. No model, no network.

    render(review, prior=None) → PDF bytes      filename(review)      content_disposition(name)
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

from ..meeting_report import CLASSIFICATION, FONT_DIR
from .opinion import EVALUATION_TYPES, RECOMMENDATIONS, cited

FONT_FILES = ("NanumGothic.ttf", "NanumGothicBold.ttf")
F = "gothic"
SYNTHETIC = "본 보고서의 대상 기업과 데이터는 가상(합성)입니다"

NAVY = (30, 58, 95)
TEAL = (47, 122, 140)
CARD = (238, 243, 247)
HEAD = (221, 230, 238)
ALT = (245, 248, 250)
MUTED = (91, 107, 123)
LIGHT_BAR = (139, 176, 196)
TEXT = (33, 39, 46)
RULE = (196, 206, 215)
WHITE = (255, 255, 255)
GREEN, AMBER, RED = (46, 122, 87), (184, 128, 28), (168, 58, 50)
REC_COLOR = {"PROCEED": GREEN, "CONDITIONAL_PROCEED": AMBER, "DO_NOT_PROCEED": RED}
LEVEL = {"HIGH": ("높음", RED), "MEDIUM": ("중간", AMBER), "LOW": ("낮음", GREEN)}
PLAN_KIND = {"actual": "실적", "budget": "예산", "forecast": "전망"}
RECORD_KIND = {"meeting": "회의", "meeting_decision": "회의 결정", "meeting_issue": "미결 쟁점", "action_item": "후속 조치",
               "article": "기사", "signal": "결정 신호", "news_event": "뉴스 사건", "advisory_review": "이전 판단"}
FIGURE_KINDS = {"calc", "fact", "check"}
GLYPHS = str.maketrans({"\u2212": "-"})  # characters NanumGothic does not have, drawn as the nearest it has


def fonts_available() -> bool:
    return all((FONT_DIR / f).is_file() for f in FONT_FILES)


def filename(review: dict[str, Any]) -> str:
    return re.sub(r'[\\/:*?"<>|]+', " ", f"{review.get('company') or '기업'}_투자심의보고서_{review.get('code') or ''}.pdf")


def content_disposition(name: str) -> str:
    return f"attachment; filename=\"advisory_report.pdf\"; filename*=UTF-8''{quote(name)}"


def render(review: dict[str, Any], prior: dict[str, Any] | None = None) -> bytes:
    r = _Review(review)
    doc = _Doc(r)
    _cover(doc, r)
    numbers = iter(range(1, 20))
    _executive(doc, r, next(numbers))
    if r.opinion.get("changes_from_prior") or prior is not None:
        _changes(doc, r, _Review(prior) if prior else None, next(numbers))
    for part in (_financials, _valuation, _commercial, _technology, _risks):
        if part(doc, r, None):  # dry check: does the section have anything to show?
            part(doc, r, next(numbers))
    _appendix(doc, r)
    return bytes(doc.pdf.output())


# ---------------------------------------------------------------------------
# Reading the stored review
# ---------------------------------------------------------------------------


class _Review:
    """The stored review with lookups into its evidence: the main analysis (the evaluated price), the others."""

    def __init__(self, review: dict[str, Any]):
        self.raw = review
        self.opinion: dict[str, Any] = review.get("opinion") or {}
        ev = review.get("evidence") or {}
        self.items: dict[str, dict[str, Any]] = ev.get("items") or {}
        analyses = [a for a in ev.get("analyses") or [] if a.get("found")]
        price = review.get("price_trillion_krw")
        self.main: dict[str, Any] = next((a for a in analyses if price is not None and (a.get("price") or {}).get("trillion_krw") == price),
                                         analyses[0] if analyses else {})
        self.others = [a for a in analyses if a is not self.main]
        self.code = review.get("code") or ""
        self.company = review.get("company") or self.main.get("company") or ""
        self.rec = review.get("recommendation") or self.opinion.get("recommendation")
        self.sections: dict[str, str] = self.opinion.get("sections") or {}

    @property
    def tables(self) -> dict[str, Any]:
        return self.main.get("tables") or {}

    @property
    def scenarios(self) -> list[dict[str, Any]]:
        return self.main.get("scenarios") or []

    def records(self) -> list[dict[str, Any]]:
        """Records the judgement rests on (meetings, decisions, articles …): the cited ones first, in the order cited, then the other meeting records the run read."""
        order = {i: n for n, i in enumerate(cited(self.opinion))}
        recs = [i for i in self.items.values() if i.get("kind") not in FIGURE_KINDS and i.get("id")]
        return sorted(recs, key=lambda i: (order.get(i["id"], len(order)), str(i.get("date") or ""), i["id"]))


def item(a: dict[str, Any] | None, key: str, scenario: str | None = None) -> dict[str, Any] | None:
    if not a:
        return None
    want = f"{a.get('analysis_id')}.{scenario + '.' if scenario else ''}{key}"
    return next((i for i in a.get("items") or [] if i.get("id") == want), None)


def disp(a: dict[str, Any] | None, key: str, scenario: str | None = None) -> str | None:
    i = item(a, key, scenario)
    return i.get("display") if i else None


def price_basis(a: dict[str, Any]) -> str:
    p = item(a, "deal.price")
    return f"{(a.get('price') or {}).get('label') or '평가 가격'} {p['display']}" if p else ""


def _when(value: Any, time: bool = False) -> str:
    if not value:
        return "—"
    if isinstance(value, datetime):
        d = value
    else:
        try:
            d = datetime.fromisoformat(str(value))
        except ValueError:
            return str(value)
    if isinstance(d, datetime) and time:
        return f"{d.year}. {d.month}. {d.day}. {d:%H:%M}"
    return f"{d.year}. {d.month}. {d.day}."


def _day(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return _when(value)
    return _when(str(value)[:10]) if value else "—"


def _short_day(value: Any) -> str:
    """2026.11.17 — a date in a narrow table column."""
    text = _day(value)
    return text.replace(". ", ".").rstrip(".") if text != "—" else text


def _main_part(display: str) -> tuple[str, str]:
    """'21,493억 원 (2.15조 원)' → ('21,493억 원', '2.15조 원') — the same figure, split for a card."""
    m = re.match(r"^(.*?)\s*\((.*)\)$", display or "")
    return (m.group(1), m.group(2)) if m else (display or "—", "")


def _num(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        return f"{v:,.0f}"
    return str(v)


def _pct(v: Any) -> str:
    return f"{v * 100:.1f}%" if isinstance(v, (int, float)) else "—"


def _unscenario(label: str) -> str:
    return re.sub(r"\s*\(시나리오\)$", "", label or "")


def _tint(c: tuple[int, int, int], k: float = 0.86) -> tuple[int, int, int]:
    return tuple(round(x + (255 - x) * k) for x in c)  # type: ignore[return-value]


def _record_text(i: dict[str, Any]) -> str:
    kind = i.get("kind")
    text = i.get("title") or i.get("text") or ""
    if kind == "meeting_decision" and i.get("speaker"):
        text = f"{text} — {i['speaker']}"
    if kind == "action_item":
        extra = " · ".join(x for x in (i.get("owner"), f"기한 {i['due']}" if i.get("due") else None, i.get("status")) if x)
        text += f" ({extra})" if extra else ""
    if kind == "signal" and i.get("text") and i.get("title"):
        text += f" — {i['text']}"
    return text


def _record_source(i: dict[str, Any]) -> str:
    return i.get("outlet") or i.get("meeting_title") or i.get("meeting") or ""


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


class _Doc:
    """A4 portrait, NAVITECH header and the document code with page numbers on every page."""

    L = 18.0

    def __init__(self, r: _Review):
        from fpdf import FPDF, FontFace
        from fpdf.enums import MethodReturnValue, XPos, YPos

        self.FontFace, self.XPos, self.YPos, self.HEIGHT = FontFace, XPos, YPos, MethodReturnValue.HEIGHT
        code = r.code
        cls_label = CLASSIFICATION.get(r.raw.get("classification"), "")
        L = self.L

        class Pdf(FPDF):
            def normalize_text(p, text: str) -> str:
                return super().normalize_text(text.translate(GLYPHS))

            def header(p) -> None:
                p.set_y(10)
                p.set_x(L)
                p.set_font(F, "B", 11)
                p.set_text_color(*NAVY)
                p.set_char_spacing(2.4)
                p.cell(80, 6, "NAVITECH")
                p.set_char_spacing(0.6)
                p.set_font(F, "", 6.8)
                p.set_text_color(*MUTED)
                p.set_xy(L, 10)
                p.cell(p.w - 2 * L, 6, "GROUP STRATEGY INTELLIGENCE", align="R")
                p.set_char_spacing(0)
                p.set_draw_color(*NAVY)
                p.set_line_width(0.45)
                p.line(L, 17.2, p.w - L, 17.2)
                p.set_y(24)

            def footer(p) -> None:
                y = p.h - 15
                p.set_draw_color(*RULE)
                p.set_line_width(0.2)
                p.line(L, y, p.w - L, y)
                p.set_font(F, "", 7)
                p.set_text_color(*MUTED)
                p.set_xy(L, y + 1.6)
                p.cell(p.w - 2 * L, 4, "NAVITECH / K-GROUP INVESTMENT REVIEW · 가상 기업 투자심의")
                p.set_xy(L, y + 1.6)
                p.cell(p.w - 2 * L, 4, f"{code} | {p.page_no():02d} / {{nb}}", align="R")
                p.set_font(F, "", 6.4)
                p.set_xy(L, y + 5.6)
                p.cell(p.w - 2 * L, 4, " · ".join(x for x in (cls_label, SYNTHETIC + ".") if x))

        logging.getLogger("fontTools").setLevel(logging.ERROR)
        pdf = Pdf(format="A4")
        pdf.add_font(F, "", str(FONT_DIR / FONT_FILES[0]))
        pdf.add_font(F, "B", str(FONT_DIR / FONT_FILES[1]))
        pdf.set_margins(L, 24, L)
        pdf.set_auto_page_break(True, margin=21)
        pdf.set_title(f"{r.company} 인수 투자심의 보고서 {code}")
        pdf.set_author("Navitech 경영자문 Agent")
        pdf.set_creator("Navitech 경영자문 Agent")
        self.pdf = pdf
        self.scenario_names = {s["id"]: s.get("name") or s["id"] for s in r.scenarios}

    # ── primitives ──────────────────────────────────────────────────────────

    @property
    def epw(self) -> float:
        return self.pdf.epw

    def font(self, size: float, bold: bool = False, color: tuple[int, int, int] = TEXT) -> None:
        self.pdf.set_font(F, "B" if bold else "", size)
        self.pdf.set_text_color(*color)

    def height(self, w: float, lh: float, text: str, size: float, bold: bool = False) -> float:
        self.font(size, bold)
        return self.pdf.multi_cell(w, lh, text, dry_run=True, output=self.HEIGHT, align="L")

    def text_at(self, x: float, y: float, w: float, lh: float, text: str, size: float, bold: bool = False,
                color: tuple[int, int, int] = TEXT, align: str = "L") -> float:
        self.font(size, bold, color)
        self.pdf.set_xy(x, y)
        self.pdf.multi_cell(w, lh, text, align=align, new_x=self.XPos.LMARGIN, new_y=self.YPos.NEXT)
        return self.pdf.get_y()

    def fit(self, text: str, w: float, size: float, bold: bool = True, floor: float = 8) -> float:
        while size > floor:
            self.font(size, bold)
            if self.pdf.get_string_width(text) <= w:
                break
            size -= 0.5
        return size

    def ensure(self, h: float) -> None:
        if self.pdf.will_page_break(h):
            self.pdf.add_page()

    def gap(self, mm: float = 3) -> None:
        self.pdf.ln(mm)

    def badge(self, x: float, y: float, text: str, color: tuple[int, int, int], size: float = 8.5, h: float = 6) -> float:
        self.font(size, True, WHITE)
        w = self.pdf.get_string_width(text) + 6
        self.pdf.set_fill_color(*color)
        self.pdf.rect(x, y, w, h, style="F", round_corners=True, corner_radius=1.2)
        self.pdf.set_xy(x, y)
        self.pdf.cell(w, h, text, align="C")
        return w

    # ── blocks ──────────────────────────────────────────────────────────────

    def section(self, number: int | str, label: str, title: str, subtitle: str = "", need: float = 80) -> None:
        """A section opener; on a fresh page unless more than half the page is free and its first `need` mm fit."""
        pdf = self.pdf
        if pdf.page == 0 or pdf.get_y() > 135 or pdf.will_page_break(need + 12):
            pdf.add_page()
        else:
            pdf.ln(5)
            pdf.set_draw_color(*RULE)
            pdf.set_line_width(0.25)
            pdf.line(self.L, pdf.get_y(), self.L + self.epw, pdf.get_y())
            pdf.ln(7)
        self.font(8, True, TEAL)
        pdf.set_char_spacing(1.2)
        pdf.cell(0, 5, f"{number:02d} / {label}" if isinstance(number, int) else f"{number} / {label}",
                 new_x=self.XPos.LMARGIN, new_y=self.YPos.NEXT)
        pdf.set_char_spacing(0)
        pdf.ln(1.5)
        self.font(19, True, NAVY)
        pdf.multi_cell(0, 9, title, new_x=self.XPos.LMARGIN, new_y=self.YPos.NEXT)
        if subtitle:
            pdf.ln(0.5)
            self.font(9.5, False, MUTED)
            pdf.multi_cell(0, 5, subtitle, new_x=self.XPos.LMARGIN, new_y=self.YPos.NEXT)
        pdf.ln(5)

    def sub(self, text: str, room: float = 34) -> None:
        pdf = self.pdf
        self.ensure(room + 10)
        pdf.ln(2.5)
        y = pdf.get_y()
        pdf.set_fill_color(*NAVY)
        pdf.rect(self.L, y + 0.9, 1.2, 4.6, style="F")
        self.font(11, True, NAVY)
        pdf.set_xy(self.L + 3.6, y)
        pdf.cell(0, 6.4, text, new_x=self.XPos.LMARGIN, new_y=self.YPos.NEXT)
        pdf.ln(1.8)

    def para(self, text: str, size: float = 9.8, color: tuple[int, int, int] = TEXT, lh: float | None = None) -> None:
        if not text:
            return
        self.font(size, False, color)
        self.pdf.set_x(self.L)
        self.pdf.multi_cell(0, lh or size * 0.56, text, align="L", new_x=self.XPos.LMARGIN, new_y=self.YPos.NEXT)

    def note(self, text: str) -> None:
        self.para(text, size=7.8, color=MUTED, lh=4)

    def bullets(self, lines: list[str], size: float = 9.4, mark: str = "•") -> None:
        pdf = self.pdf
        for line in lines:
            self.font(size, False, TEXT)
            lh = size * 0.55
            self.ensure(lh * 2)
            pdf.set_x(self.L + 1.5)
            pdf.cell(4.5, lh, mark)
            pdf.multi_cell(self.epw - 6, lh, line, align="L", new_x=self.XPos.LMARGIN, new_y=self.YPos.NEXT)
            pdf.ln(0.8)

    def callout(self, text: str, label: str = "검토 의견") -> None:
        """The judgement's own words for a section, set apart from the figures."""
        if not text:
            return
        pdf = self.pdf
        w = self.epw - 10
        h = 6.5 + self.height(w, 5.4, text, 9.8) + 4.5
        self.ensure(h + 3)
        x, y = self.L, pdf.get_y()
        pdf.set_fill_color(*CARD)
        pdf.rect(x, y, self.epw, h, style="F")
        pdf.set_fill_color(*TEAL)
        pdf.rect(x, y, 1.4, h, style="F")
        self.text_at(x + 6, y + 3, w, 3.6, label, 7.6, True, TEAL)
        self.text_at(x + 6, y + 7.5, w, 5.4, text, 9.8, False, TEXT)
        pdf.set_y(y + h + 4)

    def table(self, headers: list[str] | None, rows: list[list[Any]], widths: list[float], align: list[str] | None = None,
              *, size: float = 8.6, zebra: bool = True, head_align: str = "CENTER") -> None:
        """Rows of str or (str, FontFace); text wraps inside its cell."""
        if not rows:
            return
        pdf = self.pdf
        self.font(size, False, TEXT)
        pdf.set_fill_color(*WHITE)  # a table takes the current fill colour as its rows' background
        pdf.set_draw_color(*RULE)
        pdf.set_line_width(0.15)
        lh = size * 0.5
        self.ensure(lh * 2 + 12)
        with pdf.table(col_widths=widths, width=sum(widths), align="LEFT", text_align=tuple(align or ["LEFT"] * len(widths)),
                       line_height=lh, headings_style=self.FontFace(family=F, emphasis="BOLD", color=NAVY, fill_color=HEAD),
                       cell_fill_color=ALT if zebra else None, cell_fill_mode="ROWS" if zebra else "NONE",
                       borders_layout="HORIZONTAL_LINES", padding=(1.6, 2.0), v_align="MIDDLE",
                       first_row_as_headings=bool(headers)) as t:
            if headers:
                head = t.row()
                for h in headers:
                    head.cell(h, align=head_align)
            for values in rows:
                row = t.row()
                for v in values:
                    if isinstance(v, tuple):
                        row.cell(v[0] if v[0] is not None else "", style=v[1])
                    else:
                        row.cell("" if v is None else str(v))
        pdf.ln(3)

    def ids_face(self) -> Any:
        return self.FontFace(size_pt=6.5, color=MUTED)

    def bold_face(self, color: tuple[int, int, int] = NAVY) -> Any:
        return self.FontFace(emphasis="BOLD", color=color)

    def facts(self, rows: list[dict[str, Any] | None], label: dict[str, str] | None = None) -> None:
        """Figures of the analysis as stored: label, display, basis, id."""
        rows = [i for i in rows if i]
        if not rows:
            return
        ids = self.ids_face()

        def name(i: dict[str, Any]) -> str:
            if i["id"] in (label or {}):
                return label[i["id"]]  # type: ignore[index]
            text = i.get("label") or ""
            if i.get("scenario"):
                text = f"[{i['scenario']}] {self.scenario_names.get(i['scenario'], '')} · {_unscenario(text)}"
            return text

        self.table(["항목", "값", "산식 · 비고", "근거 id"],
                   [[name(i), self.value_cell(i.get("display") or ""), i.get("basis") or "", (i["id"], ids)] for i in rows],
                   [50, 38, 48, 38], ["LEFT", "LEFT", "LEFT", "LEFT"])

    def value_cell(self, text: str) -> Any:
        """A figure in bold; a sentence-long stored value in regular weight."""
        return (text, self.bold_face()) if len(text) <= 22 else text

    def kpis(self, cards: list[tuple[str, str, str]]) -> None:
        if not cards:
            return
        pdf = self.pdf
        n, gap = len(cards), 3.5
        w = (self.epw - gap * (n - 1)) / n
        notes = [self.height(w - 6, 3.5, note, 7.2) if note else 0 for _, _, note in cards]
        labels = [self.height(w - 6, 3.6, label, 7.6, True) for label, _, _ in cards]
        top = max(labels)
        h = 4 + top + 2 + 8 + 1.5 + max(notes) + 3.5
        self.ensure(h + 4)
        y = pdf.get_y()
        for k, (label, value, note) in enumerate(cards):
            x = self.L + k * (w + gap)
            pdf.set_fill_color(*CARD)
            pdf.rect(x, y, w, h, style="F")
            pdf.set_fill_color(*NAVY)
            pdf.rect(x, y, w, 0.9, style="F")
            self.text_at(x + 3, y + 4, w - 6, 3.6, label, 7.6, True, MUTED)
            size = self.fit(value, w - 6, 15)
            self.font(size, True, NAVY)
            pdf.set_xy(x + 3, y + 4 + top + 2)
            pdf.cell(w - 6, 8, value)
            if note:
                self.text_at(x + 3, y + 4 + top + 2 + 9.5, w - 6, 3.5, note, 7.2, False, MUTED)
        pdf.set_y(y + h + 4)


# ---------------------------------------------------------------------------
# Cover
# ---------------------------------------------------------------------------


def _cover(doc: _Doc, r: _Review) -> None:
    pdf = doc.pdf
    pdf.add_page()
    a = r.main
    pdf.set_y(44)
    doc.font(9, True, TEAL)
    pdf.set_char_spacing(2.2)
    pdf.cell(0, 5, "M&A INVESTMENT COMMITTEE", new_x=doc.XPos.LMARGIN, new_y=doc.YPos.NEXT)
    pdf.set_char_spacing(0)
    pdf.set_fill_color(*TEAL)
    pdf.rect(doc.L, pdf.get_y() + 2.5, 22, 0.8, style="F")
    pdf.ln(9)
    doc.font(30, True, NAVY)
    pdf.multi_cell(0, 14, f"{r.company} 인수\n투자심의 보고서", align="L", new_x=doc.XPos.LMARGIN, new_y=doc.YPos.NEXT)
    pdf.ln(3)
    doc.font(11.5, False, MUTED)
    pdf.cell(0, 6, "Investment Committee Review / Executive Decision", new_x=doc.XPos.LMARGIN, new_y=doc.YPos.NEXT)
    pdf.ln(7)
    rec_label = RECOMMENDATIONS.get(r.rec, r.rec or "—")
    y = pdf.get_y()
    doc.font(8, True, MUTED)
    pdf.set_xy(doc.L, y + 1.6)
    pdf.cell(16, 6, "권고")
    doc.badge(doc.L + 14, y, rec_label, REC_COLOR.get(r.rec, NAVY), size=11, h=9)
    pdf.set_y(y + 18)

    eval_label = EVALUATION_TYPES.get(r.raw.get("evaluation_type"), r.raw.get("evaluation_type") or "")
    asking = item(a, "deal.asking")
    stake = re.search(r"지분\s*\d+(?:\.\d+)?%", (asking or {}).get("basis") or "")
    target = f"{r.company} {stake.group(0)} 인수" if stake else f"{r.company} 인수"
    rows = [
        ("문서 구분", f"M&A 투자심의 · {eval_label}" if eval_label else "M&A 투자심의"),
        ("문서번호", r.code),
        ("보고 대상", "경영진 및 투자심의위원회"),
        ("검토 대상", f"{a['acquirer']} · {target}" if a.get("acquirer") else target),
        ("평가 기준", price_basis(a) or "—"),
        ("권고", rec_label),
    ]
    if r.raw.get("prior_code"):
        rows.append(("이전 판단", r.raw["prior_code"]))
    rows += [
        ("평가일", _when(r.raw.get("created_at"), time=True)),
        ("근거 기준일", _day(r.raw.get("evidence_as_of"))),
        ("열람 등급", CLASSIFICATION.get(r.raw.get("classification"), "—")),
    ]
    label_face = doc.FontFace(family=F, emphasis="BOLD", color=NAVY, fill_color=HEAD)
    doc.font(9.4, False, TEXT)
    pdf.set_draw_color(*WHITE)
    pdf.set_line_width(0.6)
    with pdf.table(col_widths=(34, doc.epw - 34), width=doc.epw, align="LEFT", text_align=("LEFT", "LEFT"), line_height=5,
                   first_row_as_headings=False, borders_layout="HORIZONTAL_LINES", padding=(2.0, 3.2),
                   cell_fill_color=ALT, cell_fill_mode="ALL", v_align="MIDDLE") as t:
        for label, value in rows:
            row = t.row()
            row.cell(label, style=label_face)
            row.cell(value, style=doc.bold_face(REC_COLOR.get(r.rec, NAVY)) if label == "권고" else None)

    disclaimer = (f"{SYNTHETIC}. 실존 기업 · 거래와 무관합니다.\n"
                  + (f"데이터 출처: {a['data_source']}" + (f" (기준일 {_day(a.get('data_as_of'))})" if a.get("data_as_of") else "") + "\n"
                     if a.get("data_source") else "")
                  + "이 문서는 검증을 거쳐 저장된 경영자문 판단과 그 근거를 옮긴 것이며, 문서 작성 과정에서 판단이나 수치를 새로 만들지 않았습니다.")
    w = doc.epw - 8
    h = doc.height(w, 4.3, disclaimer, 7.8) + 6
    y = max(pdf.get_y() + 8, pdf.h - 24 - h)
    pdf.set_fill_color(*CARD)
    pdf.rect(doc.L, y, doc.epw, h, style="F")
    doc.text_at(doc.L + 4, y + 3, w, 4.3, disclaimer, 7.8, False, MUTED)


# ---------------------------------------------------------------------------
# 01 Executive decision
# ---------------------------------------------------------------------------


def _executive(doc: _Doc, r: _Review, number: int) -> None:
    pdf, a, o = doc.pdf, r.main, r.opinion
    eval_label = EVALUATION_TYPES.get(r.raw.get("evaluation_type"), "")
    doc.section(number, "EXECUTIVE DECISION", "경영진 투자심의 결론",
                " · ".join(x for x in (f"{r.company} 인수", eval_label, f"{price_basis(a)} 기준" if price_basis(a) else "") if x))

    # recommendation box
    rec_label = RECOMMENDATIONS.get(r.rec, r.rec or "—")
    color = REC_COLOR.get(r.rec, NAVY)
    left, pad = 40, 5
    w = doc.epw - left - 2 * pad
    hh = doc.height(w, 6.2, o.get("headline") or "", 12, True)
    sh = doc.height(w, 5.3, o.get("summary") or "", 9.5) if o.get("summary") else 0
    h = max(34, pad + hh + (3 if sh else 0) + sh + pad)
    doc.ensure(h + 4)
    y = pdf.get_y()
    pdf.set_fill_color(*CARD)
    pdf.rect(doc.L, y, doc.epw, h, style="F")
    pdf.set_fill_color(*color)
    pdf.rect(doc.L, y, left, h, style="F")
    doc.text_at(doc.L + 4, y + h / 2 - 9, left - 8, 4, "RECOMMENDATION", 6.4, True, WHITE)
    size = doc.fit(rec_label, left - 8, 17)
    doc.text_at(doc.L + 4, y + h / 2 - 4, left - 8, 8, rec_label, size, True, WHITE)
    doc.text_at(doc.L + 4, y + h / 2 + 5, left - 8, 4, "권고", 7.5, False, WHITE)
    yy = doc.text_at(doc.L + left + pad, y + pad, w, 6.2, o.get("headline") or "", 12, True, NAVY)
    if sh:
        doc.text_at(doc.L + left + pad, yy + 3, w, 5.3, o["summary"], 9.5, False, TEXT)
    pdf.set_y(y + h + 5)

    # KPI cards
    cards = []
    p = item(a, "deal.price")
    if p:
        cards.append(("평가 가격", p["display"], (a.get("price") or {}).get("label") or ""))
    eq = item(a, "dcf.equity_value")
    if eq:
        main, rest = _main_part(eq["display"])
        cards.append(("DCF 주주가치", main, rest))
    prem = item(a, "price.premium_vs_dcf")
    if prem:
        cards.append(("가격 프리미엄", prem["display"], "DCF 주주가치 대비"))
    other = next((x for x in r.others if item(x, "price.premium_vs_dcf")), None)
    if other:
        cards.append((f"{price_basis(other)} 프리미엄", disp(other, "price.premium_vs_dcf") or "—", "DCF 주주가치 대비"))
    elif item(a, "fin.operating_profit_current"):
        op_ = item(a, "fin.operating_profit_current")
        cards.append((op_["label"], op_["display"], disp(a, "fin.operating_margin_current") and f"영업이익률 {disp(a, 'fin.operating_margin_current')}" or ""))
    doc.kpis(cards[:4])

    personas = o.get("personas") or []
    if personas:
        doc.sub("경영진 관점별 판단")
        rows = []
        for v in personas:
            lines = [v.get("judgement") or ""]
            lines += [f"· {c.get('statement')}" for c in v.get("key_points") or [] if c.get("statement")]
            if v.get("deal_breakers"):
                lines.append(f"중단 조건: {v['deal_breakers']}")
            if v.get("would_change_if"):
                lines.append(f"판단이 바뀌는 조건: {v['would_change_if']}")
            ids = list(dict.fromkeys([*(v.get("evidence") or []), *(i for c in v.get("key_points") or [] for i in c.get("evidence") or [])]))
            stance = v.get("stance")
            sc = REC_COLOR.get(stance, NAVY)
            rows.append([(v.get("persona") or "", doc.bold_face()),
                         (RECOMMENDATIONS.get(stance, stance or ""), doc.FontFace(emphasis="BOLD", color=sc, fill_color=_tint(sc))),
                         "\n".join(x for x in lines if x), ("\n".join(ids), doc.ids_face())])
        doc.table(["관점", "입장", "판단", "근거 id"], rows, [15, 22, 99, 38], ["CENTER", "CENTER", "LEFT", "LEFT"], size=8.8)

    rationale = o.get("rationale") or []
    if rationale:
        doc.sub("핵심 판단 근거")
        doc.table(["No", "판단 근거", "근거 id"],
                  [[str(n), c.get("statement") or "", ("\n".join(c.get("evidence") or []), doc.ids_face())] for n, c in enumerate(rationale, 1)],
                  [10, 126, 38], ["CENTER", "LEFT", "LEFT"], size=8.8)


# ---------------------------------------------------------------------------
# Change from the prior judgement
# ---------------------------------------------------------------------------


def _same_price(analyses: list[dict[str, Any]], a: dict[str, Any]) -> dict[str, Any] | None:
    want = (a.get("price") or {}).get("trillion_krw")
    return next((x for x in analyses if (x.get("price") or {}).get("trillion_krw") == want), None)


COMPARE_KEYS = ("deal.price", "dcf.equity_value", "price.premium_vs_dcf", "price.gap_vs_dcf", "cust.largest_share",
                "cust.po_coverage", "plan.break_even_year", "plan.funding_need")
SCENARIO_KEYS = ("dcf.equity_value", "delta.equity_value", "price.premium_vs_dcf", "cust.po_coverage", "plan.break_even_year")


def _changes(doc: _Doc, r: _Review, prior: _Review | None, number: int) -> None:
    pdf = doc.pdf
    doc.section(number, "CHANGE FROM PRIOR", "이전 판단 대비 변경",
                f"이전 판단 {prior.code if prior else r.raw.get('prior_code') or ''} 이후 새로 반영된 근거와 달라진 수치".strip())

    # prior → current
    w = (doc.epw - 14) / 2
    h = 25
    doc.ensure(h + 4)
    y = pdf.get_y()
    sides = [("이전 판단", prior.code if prior else r.raw.get("prior_code") or "—", prior.raw.get("created_at") if prior else None,
              prior.rec if prior else None),
             ("현재 판단", r.code, r.raw.get("created_at"), r.rec)]
    for k, (label, code, when, rec) in enumerate(sides):
        x = doc.L + k * (w + 14)
        pdf.set_fill_color(*CARD)
        pdf.rect(x, y, w, h, style="F")
        doc.text_at(x + 4, y + 3.5, w - 8, 4, label, 7.6, True, MUTED)
        doc.text_at(x + 4, y + 8, w - 8, 5, f"{code}  ·  {_when(when)}" if when else code, 9.4, True, NAVY)
        if rec:
            doc.badge(x + 4, y + 15, RECOMMENDATIONS.get(rec, rec), REC_COLOR.get(rec, NAVY), size=9, h=6.5)
        else:
            doc.text_at(x + 4, y + 16, w - 8, 5, "저장된 이전 판단을 함께 받지 않았습니다", 8, False, MUTED)
    doc.font(16, True, NAVY)
    pdf.set_xy(doc.L + w, y + h / 2 - 4)
    pdf.cell(14, 8, "→", align="C")
    pdf.set_y(y + h + 4)

    if r.opinion.get("changes_from_prior"):
        doc.callout(r.opinion["changes_from_prior"], "변경 사유")

    # figures, prior vs current
    if prior is not None:
        prior_analyses = [prior.main, *prior.others]
        rows = []
        for a in [r.main, *r.others]:
            pa = _same_price(prior_analyses, a)
            prefix = "" if a is r.main else f"[{price_basis(a)}] "
            for key in COMPARE_KEYS:
                cur, old = item(a, key), item(pa, key)
                if not cur and not old:
                    continue
                if a is not r.main and key not in ("price.premium_vs_dcf", "price.gap_vs_dcf"):
                    continue
                rows.append([prefix + (cur or old)["label"], _main_part(old["display"])[0] if old else "—",
                             (_main_part((cur or {}).get("display") or "—")[0], doc.bold_face()), ((cur or old)["id"], doc.ids_face())])
        prior_scen = {s.get("name"): s for s in prior.scenarios}
        for s in r.scenarios:
            ps = prior_scen.get(s.get("name"))
            for key in SCENARIO_KEYS:
                cur = item(r.main, key, s["id"])
                if not cur:
                    continue
                old = item(prior.main, key, ps["id"]) if ps else None
                label = _unscenario(cur["label"])
                rows.append([f"[{s['id']}] {s.get('name')} · {label}", _main_part(old["display"])[0] if old else "—",
                             (_main_part(cur["display"])[0] if key != "delta.equity_value" else cur["display"], doc.bold_face()),
                             (cur["id"], doc.ids_face())])
        if rows:
            doc.sub("주요 수치 비교")
            doc.table(["항목", "이전 판단", "현재 판단", "근거 id"], rows, [70, 30, 36, 38],
                      ["LEFT", "RIGHT", "RIGHT", "LEFT"], size=8.4)
            doc.note("이전 값이 없는 행은 이전 판단에서 계산하지 않은 시나리오 · 지표입니다.")

        old_ids = set(prior.items)
        new = [i for i in r.records() if i["id"] not in old_ids]
        if new:
            doc.sub("새로 반영된 근거")
            doc.table(["구분", "일자", "출처", "내용", "id"],
                      [[RECORD_KIND.get(i.get("kind"), i.get("kind") or ""), _short_day(i.get("date")), _record_source(i), _record_text(i),
                        (i["id"], doc.ids_face())] for i in new],
                      [16, 23, 30, 67, 38], ["CENTER", "CENTER", "LEFT", "LEFT", "LEFT"], size=8.4)
            for i in new:
                if i.get("kind") == "article" and i.get("text"):
                    doc.note(f"{i['id']} — {i['text']}")


# ---------------------------------------------------------------------------
# Financials
# ---------------------------------------------------------------------------


SCENARIO_COLORS = [TEAL, (178, 96, 72), (122, 104, 160), (96, 140, 88)]


def _revenue_chart(doc: _Doc, plan: list[dict[str, Any]], scenarios: list[dict[str, Any]]) -> None:
    pdf = doc.pdf
    values = [p["revenue"] for p in plan if isinstance(p.get("revenue"), (int, float))]
    for s in scenarios:
        values += [p["revenue"] for p in s.get("plan") or [] if isinstance(p.get("revenue"), (int, float))]
    if not values or max(values) <= 0:
        return
    h_chart, top_pad = 50, 6
    doc.ensure(h_chart + top_pad + 20)
    x0, y0 = doc.L + 2, pdf.get_y() + top_pad
    w = doc.epw - 4
    base = y0 + h_chart
    vmax = max(values) * 1.08
    slot = w / len(plan)
    bw = slot * 0.5
    pdf.set_draw_color(*RULE)
    pdf.set_line_width(0.15)
    for k in range(1, 4):  # light guide lines
        gy = base - h_chart * k / 4
        pdf.set_dash_pattern(0.6, 0.8)
        pdf.line(x0, gy, x0 + w, gy)
    pdf.set_dash_pattern()
    centers = []
    for k, p in enumerate(plan):
        cx = x0 + slot * k + slot / 2
        centers.append(cx)
        v = p.get("revenue") or 0
        bh = max(0.0, v / vmax * h_chart)
        pdf.set_fill_color(*(NAVY if p.get("kind") == "forecast" else LIGHT_BAR))
        pdf.rect(cx - bw / 2, base - bh, bw, bh, style="F")
        doc.font(7, True, NAVY)
        pdf.set_xy(cx - slot / 2, base - bh - 4.6)
        pdf.cell(slot, 4, _num(v), align="C")
        doc.font(7.6, True, TEXT)
        pdf.set_xy(cx - slot / 2, base + 1.2)
        pdf.cell(slot, 4, str(p.get("year")), align="C")
        doc.font(6.6, False, MUTED)
        pdf.set_xy(cx - slot / 2, base + 4.8)
        pdf.cell(slot, 3.5, PLAN_KIND.get(p.get("kind"), p.get("kind") or ""), align="C")
    pdf.set_draw_color(*NAVY)
    pdf.set_line_width(0.3)
    pdf.line(x0, base, x0 + w, base)
    years = {p.get("year"): c for p, c in zip(plan, centers)}
    for n, s in enumerate(scenarios):
        color = SCENARIO_COLORS[n % len(SCENARIO_COLORS)]
        pts = [(years[p["year"]], base - p["revenue"] / vmax * h_chart) for p in s.get("plan") or []
               if p.get("year") in years and p.get("kind") == "forecast" and isinstance(p.get("revenue"), (int, float))]
        pdf.set_draw_color(*color)
        pdf.set_fill_color(*color)
        pdf.set_line_width(0.45)
        for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
            pdf.line(xa, ya, xb, yb)
        for xa, ya in pts:
            pdf.circle(xa, ya, 0.9, style="F")
    # legend
    ly = base + 10.5
    items = [(LIGHT_BAR, "실적 · 예산"), (NAVY, "사업계획 전망")] + [
        (SCENARIO_COLORS[n % len(SCENARIO_COLORS)], f"시나리오 {s['id']}: {s.get('name')}") for n, s in enumerate(scenarios)]
    x = x0
    doc.font(7.2, False, MUTED)
    for color, label in items:
        pdf.set_fill_color(*color)
        pdf.rect(x, ly + 0.9, 3, 2.6, style="F")
        pdf.set_xy(x + 4, ly)
        tw = pdf.get_string_width(label)
        pdf.cell(tw + 1, 4.4, label)
        x += tw + 10
    doc.font(7.2, False, MUTED)
    pdf.set_xy(x0, ly)
    pdf.cell(w, 4.4, "매출액 (억 원)", align="R")
    pdf.set_y(ly + 8)


def _plan_table(doc: _Doc, plan: list[dict[str, Any]], *, lost: bool = False) -> None:
    head = ["구분"] + [f"{p.get('year')}\n{PLAN_KIND.get(p.get('kind'), '')}" for p in plan]
    label_w = 30
    cw = (doc.epw - label_w) / max(1, len(plan))
    rows = [["매출액"] + [_num(p.get("revenue")) for p in plan],
            ["영업이익"] + [_num(p.get("operating_profit")) for p in plan],
            ["영업이익률"] + [f"{p['operating_margin']:.1f}%" if isinstance(p.get("operating_margin"), (int, float)) else "—" for p in plan],
            ["자유현금흐름"] + [_num(p.get("fcf")) for p in plan]]
    if lost and any(p.get("lost_customer_revenue") for p in plan):
        rows.append(["잃는 고객 매출"] + [_num(p.get("lost_customer_revenue")) if p.get("lost_customer_revenue") is not None else "—" for p in plan])
    face = doc.bold_face()
    rows = [[(row[0], face), *row[1:]] for row in rows]
    doc.table(head, rows, [label_w] + [cw] * len(plan), ["LEFT"] + ["RIGHT"] * len(plan), size=8.2)


def _financials(doc: _Doc, r: _Review, number: int | None) -> bool:
    a = r.main
    plan = r.tables.get("plan") or []
    keys = ("fin.revenue_current", "fin.operating_profit_current", "fin.operating_margin_current", "fin.revenue_cagr", "fin.ebitda",
            "fin.adjusted_ebitda", "plan.revenue_next", "plan.revenue_last", "plan.operating_profit_last", "plan.fcf_last",
            "plan.break_even_year", "plan.funding_need")
    figures = [item(a, k) for k in keys]
    if number is None:
        return bool(plan or any(figures) or r.sections.get("financial"))
    doc.section(number, "FINANCIAL PERFORMANCE", "재무 실적과 현금흐름", "사업계획 기준 매출 · 손익 · 자유현금흐름 (단위: 억 원)",
                need=150 if plan else 80)
    doc.callout(r.sections.get("financial", ""))
    if plan:
        doc.sub("연도별 매출", room=80)
        _revenue_chart(doc, plan, r.scenarios)
        doc.sub("손익과 현금흐름 (억 원)")
        _plan_table(doc, plan)
        premise = (a.get("assumptions") or {}).get("forecast_premise")
        if premise:
            doc.note(f"사업계획 전제: {premise}")
    for s in r.scenarios:
        if s.get("plan"):
            doc.sub(f"시나리오 {s['id']}: {s.get('name')} (억 원)")
            _plan_table(doc, s["plan"], lost=True)
            if s.get("notes"):
                doc.note(" / ".join(s["notes"]))
    if any(figures):
        doc.sub("주요 재무 지표")
        doc.facts(figures + [item(a, k, s["id"]) for s in r.scenarios for k in ("plan.break_even_year", "plan.funding_need")])
    return True


# ---------------------------------------------------------------------------
# Valuation and price
# ---------------------------------------------------------------------------


def _price_axis(doc: _Doc, r: _Review) -> None:
    """Prices against the DCF value on one axis: the WACC × growth range as a band, prices above, values below."""
    pdf, a = doc.pdf, r.main
    low, high = item(a, "val.sensitivity_low"), item(a, "val.sensitivity_high")
    eq = item(a, "dcf.equity_value")
    above: list[tuple[float, str, str, tuple[int, int, int]]] = []
    below: list[tuple[float, str, str, tuple[int, int, int]]] = []
    seen = set()
    for i, label, color in ((item(a, "deal.asking"), "최초 요구가", RED), (item(a, "deal.proposal"), "제안가", TEAL)):
        if i and isinstance(i.get("value"), (int, float)):
            above.append((i["value"], label, i["display"], color))
            seen.add(round(i["value"]))
    for x in [a, *r.others]:
        p = item(x, "deal.price")
        if p and isinstance(p.get("value"), (int, float)) and round(p["value"]) not in seen:
            above.append((p["value"], (x.get("price") or {}).get("label") or "평가 가격", p["display"], AMBER))
            seen.add(round(p["value"]))
    if eq and isinstance(eq.get("value"), (int, float)):
        below.append((eq["value"], "DCF 주주가치", _main_part(eq["display"])[0], NAVY))
    for s in r.scenarios:
        se = item(a, "dcf.equity_value", s["id"])
        if se and isinstance(se.get("value"), (int, float)):
            below.append((se["value"], f"{s['id']} 주주가치", _main_part(se["display"])[0], MUTED))
    values = [v for v, *_ in above + below]
    band = (low["value"], high["value"]) if low and high else None
    if band:
        values += list(band)
    if len(values) < 2:
        return
    lo, hi = min(values), max(values)
    span = hi - lo or hi or 1
    lo, hi = max(0.0, lo - span * 0.1), hi + span * 0.1
    step = 5000 if hi - lo > 12000 else 2500 if hi - lo > 5000 else 1000
    lo, hi = (lo // step) * step, (hi // step + 1) * step
    x0, w = doc.L + 6, doc.epw - 12

    def xs(v: float) -> float:
        return x0 + (v - lo) / (hi - lo) * w

    def width(label: str, value: str) -> float:
        doc.font(6.8)
        a_ = pdf.get_string_width(label)
        doc.font(7.6, True)
        return max(a_, pdf.get_string_width(value)) + 3

    def tiers(points):
        """Labels side by side where they fit, otherwise one row further from the axis."""
        out, ends = [], []
        for v, label, value, color in sorted(points):
            x, tw = xs(v), width(label, value)
            left = max(doc.L, min(x - tw / 2, doc.L + doc.epw - tw))
            row = next((n for n, e in enumerate(ends) if e + 1.5 < left), len(ends))
            ends[row:row + 1] = [left + tw]
            out.append((x, left, row, label, value, color, tw))
        return out, len(ends)

    def caption(left: float, y: float, tw: float, text: str, size: float, bold: bool, color: tuple[int, int, int]) -> None:
        doc.font(size, bold, color)
        pdf.set_xy(left, y)
        pdf.cell(tw, 3.8, text, align="C")

    up, n_up = tiers(above)
    down, n_down = tiers(below)
    tier = 8.6
    doc.ensure(n_up * tier + 18 + n_down * tier + 8)
    axis = pdf.get_y() + n_up * tier + 6
    if band:
        pdf.set_fill_color(*_tint(LIGHT_BAR, 0.55))
        pdf.rect(xs(band[0]), axis - 3.5, xs(band[1]) - xs(band[0]), 7, style="F")
    pdf.set_draw_color(*NAVY)
    pdf.set_line_width(0.35)
    pdf.line(x0, axis, x0 + w, axis)
    v = lo
    while v <= hi + 1e-6:
        pdf.set_draw_color(*MUTED)
        pdf.set_line_width(0.2)
        pdf.line(xs(v), axis, xs(v), axis + 1.2)
        caption(xs(v) - 8, axis + 3.8, 16, f"{v / 10000:g}조", 6.4, False, MUTED)
        v += step
    for x, left, row, label, value, color, tw in up:
        ytop = axis - 5 - (row + 1) * tier
        pdf.set_draw_color(*color)
        pdf.set_line_width(0.5)
        pdf.line(x, ytop + 7.8, x, axis)
        pdf.set_fill_color(*color)
        pdf.circle(x, axis, 1.3, style="F")
        caption(left, ytop, tw, label, 6.8, False, MUTED)
        caption(left, ytop + 3.7, tw, value, 7.6, True, color)
    for x, left, row, label, value, color, tw in down:
        ytop = axis + 9.5 + row * tier
        pdf.set_draw_color(*color)
        pdf.set_line_width(0.5)
        pdf.line(x, axis, x, ytop - 0.4)
        pdf.set_fill_color(*color)
        pdf.rect(x - 1.2, axis - 1.2, 2.4, 2.4, style="F")
        caption(left, ytop, tw, value, 7.6, True, color)
        caption(left, ytop + 3.8, tw, label, 6.8, False, MUTED)
    pdf.set_y(axis + 9.5 + n_down * tier + 1)
    if band:
        doc.note(f"음영: WACC × 영구성장률 민감도 범위의 DCF 주주가치 ({low['display']} ~ {high['display']})")
    doc.gap(1)


def _valuation(doc: _Doc, r: _Review, number: int | None) -> bool:
    a = r.main
    keys = ("dcf.enterprise_value", "val.net_debt", "dcf.equity_value", "dcf.terminal_share", "val.sensitivity_low", "val.sensitivity_high",
            "price.premium_vs_dcf", "price.gap_vs_dcf", "val.ev_to_revenue")
    figures = [item(a, k) for k in keys]
    sens = r.tables.get("sensitivity") or {}
    checks = a.get("checks") or []
    if number is None:
        return bool(any(figures) or sens.get("rows") or checks or r.sections.get("valuation"))
    asm = a.get("assumptions") or {}
    sub = (f"DCF 주주가치와 평가 가격의 비교 · WACC {_pct(asm.get('wacc'))} · 영구성장률 {_pct(asm.get('terminal_growth'))}"
           if asm.get("wacc") is not None else "DCF 주주가치와 평가 가격의 비교")
    doc.section(number, "VALUATION & PRICE", "기업가치와 가격", sub, need=125)
    doc.callout(r.sections.get("valuation", ""))
    if item(a, "dcf.equity_value"):
        doc.sub("가격과 가치 범위", room=60)
        _price_axis(doc, r)
    if any(figures):
        doc.sub("DCF 산출")
        doc.facts(figures)
    priced = [x for x in [a, *r.others] if item(x, "price.premium_vs_dcf") or item(x, "price.gap_vs_dcf")]
    if len(priced) > 1:
        doc.sub("가격별 비교")
        doc.table(["가격 기준", "평가 가격", "DCF 주주가치", "프리미엄", "가격 − 주주가치", "근거 id"],
                  [[((x.get("price") or {}).get("label") or "", doc.bold_face()), disp(x, "deal.price") or "—",
                    _main_part(disp(x, "dcf.equity_value") or "—")[0], (disp(x, "price.premium_vs_dcf") or "—", doc.bold_face()),
                    _main_part(disp(x, "price.gap_vs_dcf") or "—")[0], ((item(x, "price.premium_vs_dcf") or {}).get("id", ""), doc.ids_face())]
                   for x in priced],
                  [28, 24, 30, 22, 36, 34], ["LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "LEFT"])
    if sens.get("rows") and sens.get("terminal_growth"):
        doc.sub("민감도 분석 — DCF 주주가치 (억 원)")
        gs = sens["terminal_growth"]
        bw, bg = asm.get("wacc"), asm.get("terminal_growth")
        rows = []
        for row in sens["rows"]:
            cells: list[Any] = [(f"WACC {_pct(row.get('wacc'))}", doc.bold_face())]
            for g, v in zip(gs, row.get("values") or []):
                base = row.get("wacc") == bw and g == bg
                cells.append((_num(v), doc.FontFace(emphasis="BOLD", color=WHITE, fill_color=NAVY)) if base else _num(v))
            rows.append(cells)
        cw = (doc.epw - 40) / len(gs)
        doc.table(["WACC \\ 영구성장률"] + [_pct(g) for g in gs], rows, [40] + [cw] * len(gs), ["LEFT"] + ["CENTER"] * len(gs), size=8.6)
        doc.note("진한 칸: 기준 가정 (WACC · 영구성장률)")
    if r.scenarios:
        doc.sub("시나리오별 가치")
        rows = []
        for s in r.scenarios:
            rows.append([(f"{s['id']}  {s.get('name')}", doc.bold_face()), _main_part(disp(a, "dcf.equity_value", s["id"]) or "—")[0],
                         disp(a, "delta.equity_value", s["id"]) or "—", (disp(a, "price.premium_vs_dcf", s["id"]) or "—", doc.bold_face()),
                         disp(a, "dcf.terminal_share", s["id"]) or "—"])
        doc.table(["시나리오", "DCF 주주가치", "기준 대비 변화", "가격 프리미엄", "잔존가치 비중"], rows, [52, 30, 38, 28, 26],
                  ["LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"])
    if checks:
        doc.sub("회의 기준 점검")
        doc.table(["기준", "평가", "충족 여부", "근거 id"],
                  [[c.get("criterion") or "", c.get("actual") or "",
                    ("충족", doc.bold_face(GREEN)) if c.get("met") else ("미충족", doc.bold_face(RED)), (c.get("id") or "", doc.ids_face())]
                   for c in checks],
                  [70, 40, 26, 38], ["LEFT", "LEFT", "CENTER", "LEFT"])
    return True


# ---------------------------------------------------------------------------
# Customers and contracts
# ---------------------------------------------------------------------------


def _share_bar(doc: _Doc, customers: list[dict[str, Any]]) -> None:
    pdf = doc.pdf
    shares = [(c.get("name") or "", c.get("share")) for c in customers if isinstance(c.get("share"), (int, float)) and c["share"] > 0]
    if not shares:
        return
    total = sum(s for _, s in shares)
    doc.ensure(22)
    x, y, w, h = doc.L, pdf.get_y() + 1, doc.epw, 9
    palette = [NAVY, TEAL, LIGHT_BAR, (176, 196, 210), (200, 212, 222)]
    for n, (name, share) in enumerate(shares):
        sw = share / total * w
        color = palette[min(n, len(palette) - 1)]
        pdf.set_fill_color(*color)
        pdf.rect(x, y, sw, h, style="F")
        label = f"{share:.1f}%"
        doc.font(7.4, True, WHITE if n < 2 else NAVY)
        if pdf.get_string_width(label) + 2 < sw:
            pdf.set_xy(x, y)
            pdf.cell(sw, h, label, align="C")
        doc.font(7, False, MUTED)
        if pdf.get_string_width(name) + 1 < sw:
            pdf.set_xy(x, y + h + 0.8)
            pdf.cell(sw, 3.6, name, align="C")
        x += sw
    pdf.set_y(y + h + 6.5)


def _commercial(doc: _Doc, r: _Review, number: int | None) -> bool:
    a = r.main
    customers = r.tables.get("customers") or []
    keys = ("cust.largest_revenue", "cust.largest_share", "cust.top3_share", "cust.po_next_year", "cust.largest_po", "cust.po_coverage",
            "cust.design_wins")
    figures = [item(a, k) for k in keys]
    scen = [item(a, k, s["id"]) for s in r.scenarios for k in ("cust.po_coverage", "cust.lost_revenue_next")]
    wc = [item(a, k) for k in ("wc.shortfall", "wc.price_after_adjustment", "wc.overdue_receivables_90d", "wc.aged_inventory_180d")]
    if number is None:
        return bool(customers or any(figures) or any(wc) or r.sections.get("commercial"))
    doc.section(number, "CUSTOMERS & CONTRACTS", "고객과 계약", "매출 집중도 · 계약 조건 · 확정 발주")
    doc.callout(r.sections.get("commercial", ""))
    if customers:
        doc.sub("고객별 매출 비중", room=50)
        _share_bar(doc, customers)
        po = item(a, "cust.po_next_year")
        po_head = re.sub(r"\s*\(PO\)$", "", po["label"]) if po else "다음 해 확정 발주"
        rev = item(a, "fin.revenue_current")
        rev_head = (rev["label"].split(" (")[0] if rev else "매출") + "\n(억 원)"
        doc.table(["고객", rev_head, "비중", "계약 만료", "계약 조건", po_head + "\n(억 원)"],
                  [[(c.get("name") or "", doc.bold_face()), _num(c.get("revenue")),
                    f"{c['share']:.1f}%" if isinstance(c.get("share"), (int, float)) else "—", c.get("expires") or "—", c.get("terms") or "—",
                    _num(c.get("po_next_year"))] for c in customers],
                  [34, 22, 16, 20, 52, 30], ["LEFT", "RIGHT", "RIGHT", "CENTER", "LEFT", "RIGHT"])
    if any(figures):
        doc.sub("집중도와 발주")
        doc.facts(figures)
    if any(scen):
        doc.sub("시나리오별 발주 · 매출 영향")
        doc.facts(scen)
    if any(wc):
        doc.sub("운전자본")
        doc.facts(wc)
    return True


# ---------------------------------------------------------------------------
# Technology, IP, people, integration
# ---------------------------------------------------------------------------


def _technology(doc: _Doc, r: _Review, number: int | None) -> bool:
    a = r.main
    tech = [item(a, k) for k in ("strategy.fit", "strategy.market", "tech.ttm_gain", "tech.performance_advantage", "tech.yield",
                                 "tech.core_patents_confirmed")]
    ip = [item(a, k) for k in ("tech.ip_exposure_low", "tech.ip_exposure_high", "tech.ip_exposure_high_vs_price")]
    disputes = r.tables.get("ip_disputes") or []
    people = item(a, "people.retention_open")
    integ = [item(a, k) for k in ("integ.cost", "integ.synergy")]
    breakdown = r.tables.get("integration_cost") or {}
    if number is None:
        return bool(any(tech) or any(ip) or disputes or people or any(integ) or r.sections.get("technology"))
    doc.section(number, "TECHNOLOGY · IP · PEOPLE", "기술 · 특허 · 인력 · 통합", "전략 적합성 · 기술 우위 · 지식재산 · 핵심 인력 · 통합 계획")
    doc.callout(r.sections.get("technology", ""))
    if any(tech):
        doc.sub("전략 적합성과 기술")
        doc.facts(tech)
    count = item(a, "tech.ip_disputes")
    if disputes or any(ip):
        doc.sub("특허 분쟁")
        if disputes:
            doc.table(["상대방", "쟁점", "상태"], [[(d.get("counterparty") or "", doc.bold_face()), d.get("subject") or "", d.get("status") or ""]
                                             for d in disputes], [46, 60, 68])
        doc.facts([{**count, "display": count["display"].split(" — ")[0]} if count and disputes else None, *ip])
    elif count:
        doc.sub("특허 분쟁")
        doc.facts([count])
    if people:
        doc.sub("핵심 인력")
        doc.facts([people])
    if breakdown or any(integ):
        doc.sub("통합 비용과 시너지")
        if breakdown:
            amounts = [v if isinstance(v, (int, float)) else 0 for v in breakdown.values()]
            _bar_table(doc, [[k, _num(v)] for k, v in breakdown.items()], amounts, max(amounts, default=0), "통합비용 항목", "금액 (억 원)")
        doc.facts(integ)
    return True


def _bar_table(doc: _Doc, rows: list[list[str]], values: list[float], top: float, h1: str, h2: str) -> None:
    """A two-column list with a proportional bar for each value (layout only)."""
    pdf = doc.pdf
    lw, vw, rh = 50, 26, 6.4
    bar_w = doc.epw - lw - vw - 6
    doc.ensure(rh * (len(rows) + 1) + 4)
    y = pdf.get_y()
    pdf.set_fill_color(*HEAD)
    pdf.rect(doc.L, y, doc.epw, rh, style="F")
    doc.font(8.4, True, NAVY)
    pdf.set_xy(doc.L + 2, y)
    pdf.cell(lw, rh, h1)
    pdf.set_xy(doc.L + lw, y)
    pdf.cell(vw, rh, h2, align="R")
    y += rh
    for n, ((label, value), v) in enumerate(zip(rows, values)):
        if n % 2 == 0:
            pdf.set_fill_color(*ALT)
            pdf.rect(doc.L, y, doc.epw, rh, style="F")
        doc.font(8.4, False, TEXT)
        pdf.set_xy(doc.L + 2, y)
        pdf.cell(lw - 2, rh, label)
        doc.font(8.4, True, NAVY)
        pdf.set_xy(doc.L + lw, y)
        pdf.cell(vw, rh, value, align="R")
        if top > 0:
            pdf.set_fill_color(*LIGHT_BAR)
            pdf.rect(doc.L + lw + vw + 6, y + 1.7, max(0.4, v / top * bar_w), rh - 3.4, style="F")
        y += rh
    pdf.set_draw_color(*RULE)
    pdf.set_line_width(0.15)
    pdf.line(doc.L, y, doc.L + doc.epw, y)
    pdf.set_y(y + 3)


# ---------------------------------------------------------------------------
# Risks and conditions
# ---------------------------------------------------------------------------


def _risks(doc: _Doc, r: _Review, number: int | None) -> bool:
    o = r.opinion
    risks, conditions, unc = o.get("risks") or [], o.get("conditions") or [], o.get("uncertainties") or []
    open_items = r.main.get("open_items") or []
    if number is None:
        return bool(risks or conditions or unc or open_items)
    doc.section(number, "RISKS & CONDITIONS", "위험과 권고 조건", "경영자문 판단이 짚은 위험 · 권고 조건 · 미확인 사항")
    if risks:
        doc.sub("주요 위험")
        rows = []
        for x in risks:
            label, color = LEVEL.get(x.get("level"), (x.get("level") or "", NAVY))
            rows.append([(x.get("title") or "", doc.bold_face()), (label, doc.FontFace(emphasis="BOLD", color=color, fill_color=_tint(color))),
                         x.get("assessment") or "", x.get("response") or "—", ("\n".join(x.get("evidence") or []), doc.ids_face())])
        doc.table(["위험", "수준", "평가", "대응", "근거 id"], rows, [30, 14, 54, 46, 30], ["LEFT", "CENTER", "LEFT", "LEFT", "LEFT"])
    if conditions:
        doc.sub("권고 조건")
        doc.table(["No", "조건", "다루는 위험", "근거 id"],
                  [[str(n), (c.get("condition") or "", doc.bold_face()), c.get("addresses") or "", ("\n".join(c.get("evidence") or []), doc.ids_face())]
                   for n, c in enumerate(conditions, 1)],
                  [10, 84, 46, 34], ["CENTER", "LEFT", "LEFT", "LEFT"])
    if unc:
        doc.sub("불확실성", room=24)
        doc.bullets(unc)
    if open_items:
        doc.sub("미확인 사항 (데이터 기준)", room=24)
        doc.bullets(open_items, size=9)
    return True


# ---------------------------------------------------------------------------
# Appendix
# ---------------------------------------------------------------------------


def _appendix(doc: _Doc, r: _Review) -> None:
    a = r.main
    doc.section("부록", "APPENDIX", "근거 목록과 데이터 출처", "판단이 인용한 기록 · 수치와 분석의 전제")
    records = r.records()
    if records:
        doc.sub("인용한 기록")
        doc.table(["id", "구분", "일자", "내용", "출처"],
                  [[(i["id"], doc.ids_face()), RECORD_KIND.get(i.get("kind"), i.get("kind") or ""), _short_day(i.get("date")), _record_text(i),
                    _record_source(i)] for i in records],
                  [36, 16, 22, 70, 30], ["LEFT", "CENTER", "CENTER", "LEFT", "LEFT"], size=8.2)
    figures = [r.items.get(i) for i in cited(r.opinion)]
    figures = [i for i in figures if i and i.get("kind") in FIGURE_KINDS]
    if figures:
        doc.sub("인용한 수치")
        doc.table(["id", "항목", "값"],
                  [[(i["id"], doc.ids_face()), i.get("label") or "",
                    doc.value_cell(i.get("display") or "" if i.get("kind") != "check"
                                   else f"{i.get('display') or ''} ({'충족' if i.get('met') else '미충족'})")]
                   for i in figures],
                  [44, 74, 56], ["LEFT", "LEFT", "LEFT"], size=8.2)

    doc.sub("데이터 출처")
    lines = [f"{SYNTHETIC}. 실존 기업 · 거래와 무관합니다."]
    if a.get("data_source"):
        lines.append(f"기업 데이터: {a['data_source']}" + (f" — 기준일 {_day(a.get('data_as_of'))}" if a.get("data_as_of") else ""))
    lines.append(f"근거 기준일 (인용 기록과 데이터의 최신 일자): {_day(r.raw.get('evidence_as_of'))}")
    doc.bullets(lines, size=8.8)

    asm = a.get("assumptions") or {}
    rows = [[k, v] for k, v in (("WACC", _pct(asm.get("wacc")) if asm.get("wacc") is not None else None),
                                ("영구성장률", _pct(asm.get("terminal_growth")) if asm.get("terminal_growth") is not None else None),
                                ("공헌이익률 (시나리오 매출 변화의 손익 영향)", _pct(asm.get("contribution_margin")) if asm.get("contribution_margin") is not None else None),
                                ("평가 기준일", _day(asm.get("valuation_date")) if asm.get("valuation_date") else None),
                                ("사업계획 전제", asm.get("forecast_premise"))) if v]
    if rows:
        doc.sub("가치평가 가정")
        doc.table(None, [[(k, doc.bold_face()), v] for k, v in rows], [62, 112], ["LEFT", "LEFT"])
    if r.scenarios:
        doc.sub("시나리오 가정")
        for s in r.scenarios:
            doc.para(f"{s['id']}  {s.get('name')}", size=9, color=NAVY)
            doc.bullets(s.get("notes") or [], size=8.6, mark="-")

    doc.sub("문서 정보", room=30)
    info = [["문서번호", r.code], ["평가일", _when(r.raw.get("created_at"), time=True)],
            ["작성", "Navitech 경영자문 Agent"],  # the requester stays in the store, not in a document that may be passed on
            ["열람 등급", CLASSIFICATION.get(r.raw.get("classification"), "—")]]
    if r.raw.get("meeting_codes"):
        info.append(["인용 회의", ", ".join(r.raw["meeting_codes"])])
    doc.table(None, [[(k, doc.bold_face()), v] for k, v in info], [62, 112], ["LEFT", "LEFT"])
