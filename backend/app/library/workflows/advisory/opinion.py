"""The Advisory model's judgement as a record, and the checks it must pass before it is kept (not agent-visible).

The model decides the recommendation. These checks do not judge whether that decision is right; they make sure the
record stands on what the run actually read and computed:

    - every cited id is in the run's evidence ledger (a meeting decision, an article, a figure the analysis computed);
    - every amount (조 · 억) and percentage written in the judgement matches a figure of the ledger — the analysis, the
      records read, or a difference / ratio of two of them — so a number cannot be made up or mistyped;
    - an internal criterion the analysis found unmet (e.g. the price above a meeting's ceiling) is addressed;
    - the three executive views each rest on their own figures; a conditional recommendation states its conditions;
    - a re-evaluation names the judgement it revisits and says what changed.

A failed check comes back to the model as a list of problems to fix; nothing is rewritten here.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Iterable, Iterator
from typing import Any

from ....runtime.evidence import Ledger
from .analysis import PERSONAS

RECOMMENDATIONS = {"PROCEED": "진행", "CONDITIONAL_PROCEED": "조건부 진행", "DO_NOT_PROCEED": "보류"}
EVALUATION_TYPES = {"initial": "최초 평가", "reevaluation": "재평가", "what_if": "What-if 평가"}
MAX_LISTED = 8

_NUM = r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?"
_MONEY = re.compile(_NUM + r"\s*조(?:\s*(\d{1,3}(?:,\d{3})+|\d+)\s*억)?|" + _NUM + r"\s*억")
_PERCENT = re.compile(_NUM + r"\s*%")


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


def _number(whole: str, frac: str | None) -> float:
    return float(whole.replace(",", "") + ("." + frac if frac else ""))


def amounts(text: str) -> Iterator[tuple[str, float, float]]:
    """(as written, value in 억 원, rounding allowance) for every 조 / 억 amount in `text`."""
    for m in _MONEY.finditer(text or ""):
        if m.group(1) is not None:  # 조 [억]
            value = _number(m.group(1), m.group(2)) * 10000 + (_number(m.group(3), None) if m.group(3) else 0)
            step = 0.5 * 10000 * 10 ** -len(m.group(2) or "") if not m.group(3) else 0.5
        else:
            value = _number(m.group(4), m.group(5))
            step = 0.5 * 10 ** -len(m.group(5) or "")
        yield m.group(0), value, step


def percents(text: str) -> Iterator[tuple[str, float, float]]:
    for m in _PERCENT.finditer(text or ""):
        yield m.group(0), _number(m.group(1), m.group(2)), 0.5 * 10 ** -len(m.group(2) or "")


class Figures:
    """The amounts and percentages the run's evidence states; and differences, sums and ratios of two of the figures
    the judgement cites (a comparison is made between the records it stands on, not between any two numbers)."""

    def __init__(self, ledger: Ledger, cited_ids: Iterable[str] = (), extra_amounts: Iterable[float] = ()):
        money, pcts = self._stated(ledger.items.values())
        money.update(abs(v) for v in extra_amounts)
        cited_money, cited_pcts = self._stated(ledger.items[i] for i in cited_ids if i in ledger.items)
        cited_money.update(abs(v) for v in extra_amounts)
        pairs = sorted(m for m in cited_money if m)
        for i, a in enumerate(pairs):
            for b in pairs[i + 1:]:
                money.update((b - a, a + b))
                pcts.update((a / b * 100, (b / a - 1) * 100, (1 - a / b) * 100))
        cp = sorted(cited_pcts)
        for i, a in enumerate(cp):
            for b in cp[i + 1:]:
                pcts.add(b - a)  # percentage points between two cited percentages
        self.money = sorted(money)
        self.pct = sorted(pcts)

    @staticmethod
    def _stated(items: Iterable[dict[str, Any]]) -> tuple[set[float], set[float]]:
        money: set[float] = set()
        pcts: set[float] = set()
        for item in items:
            v = item.get("value")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if item.get("unit") == "억 원":
                    money.add(abs(float(v)))
                elif item.get("unit") == "%":
                    pcts.add(abs(float(v)))
            texts = [item.get(k) for k in ("display", "basis", "label", "text", "title", "relation", "risk", "note")]
            texts += list(item.get("facts") or [])
            for text in texts:
                if isinstance(text, str):
                    money.update(abs(x) for _, x, _ in amounts(text))
                    pcts.update(abs(x) for _, x, _ in percents(text))
        return money, pcts

    @staticmethod
    def _near(values: list[float], x: float, tol: float) -> bool:
        i = bisect_left(values, x - tol)
        return i < len(values) and values[i] <= x + tol

    def has_amount(self, value: float, step: float) -> bool:
        return self._near(self.money, abs(value), max(step, abs(value) * 0.006))

    def has_percent(self, value: float, step: float) -> bool:
        return self._near(self.pct, abs(value), max(step, abs(value) * 0.01))


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


def texts(opinion: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """(where, text) of every sentence the judgement writes."""
    for key in ("headline", "summary", "changes_from_prior"):
        if opinion.get(key):
            yield key, opinion[key]
    for i, c in enumerate(opinion.get("rationale") or []):
        yield f"rationale[{i}]", c.get("statement", "")
    for p in opinion.get("personas") or []:
        where = f"personas[{p.get('persona')}]"
        for key in ("judgement", "deal_breakers", "would_change_if"):
            if p.get(key):
                yield where, p[key]
        for c in p.get("key_points") or []:
            yield where, c.get("statement", "")
    for i, r in enumerate(opinion.get("risks") or []):
        for key in ("title", "assessment", "response"):
            if r.get(key):
                yield f"risks[{i}]", r[key]
    for i, c in enumerate(opinion.get("conditions") or []):
        for key in ("condition", "addresses"):
            if c.get(key):
                yield f"conditions[{i}]", c[key]
    for i, u in enumerate(opinion.get("uncertainties") or []):
        yield f"uncertainties[{i}]", u
    for key, text in (opinion.get("sections") or {}).items():
        if text:
            yield f"sections.{key}", text


def cited(opinion: dict[str, Any]) -> list[str]:
    """Every evidence id the judgement cites, in order of first use."""
    out: list[str] = []

    def take(ids: Iterable[str] | None) -> None:
        for i in ids or []:
            i = str(i).strip()
            if i and i not in out:
                out.append(i)

    for c in opinion.get("rationale") or []:
        take(c.get("evidence"))
    for p in opinion.get("personas") or []:
        take(p.get("evidence"))
        for c in p.get("key_points") or []:
            take(c.get("evidence"))
    for r in opinion.get("risks") or []:
        take(r.get("evidence"))
    for c in opinion.get("conditions") or []:
        take(c.get("evidence"))
    take(opinion.get("evidence"))
    return out


def _key(id_: str) -> str:
    """A figure's id without the run's analysis number or a stored judgement's prefix: A2.S1.dcf.equity_value → S1.dcf.equity_value."""
    id_ = id_.split(":", 1)[-1]
    return re.sub(r"^A\d+\.", "", id_)


def _lens_key(id_: str) -> str:
    return re.sub(r"^S\d+\.", "", _key(id_))


def check(opinion: dict[str, Any], ledger: Ledger, *, prior: dict[str, Any] | None = None) -> list[str]:
    """Problems that keep the judgement from being recorded (empty = it may be kept)."""
    problems: list[str] = []
    rec = opinion.get("recommendation")
    if rec not in RECOMMENDATIONS:
        problems.append(f"recommendation은 {', '.join(RECOMMENDATIONS)} 중 하나여야 합니다.")
    if opinion.get("evaluation_type") not in EVALUATION_TYPES:
        problems.append(f"evaluation_type은 {', '.join(EVALUATION_TYPES)} 중 하나여야 합니다.")
    analyses = [a for a in ledger.analyses if a.get("found")]
    if not analyses:
        problems.append("이번 실행에서 run_mna_review로 계산한 결과가 없습니다. 수치를 Tool로 계산한 뒤 판단을 기록하세요.")
        return problems
    company = opinion.get("company") or ""
    if not any(company and (company in a["company"] or a["company"] in company) for a in analyses):
        problems.append(f"'{company}'에 대한 run_mna_review 결과가 이번 실행에 없습니다 (계산한 기업: {', '.join(sorted({a['company'] for a in analyses}))}).")
    price = opinion.get("price_trillion_krw")
    priced = {round(i["value"] / 10000, 4) for i in ledger.items.values() if _lens_key(i["id"]) == "deal.price" and isinstance(i.get("value"), (int, float))}
    if price is None or round(float(price), 4) not in priced:
        problems.append(f"price_trillion_krw({price})는 이번 실행에서 계산한 평가 가격이어야 합니다 (계산한 가격: "
                        f"{', '.join(f'{p:g}조' for p in sorted(priced)) or '없음'}). 다른 가격이면 run_mna_review로 그 가격을 먼저 계산하세요.")

    ids = cited(opinion)
    unknown = [i for i in ids if ledger.get(i) is None]
    if unknown:
        problems.append("이번 실행의 Tool 결과에 없는 근거 id를 인용했습니다: " + ", ".join(unknown[:MAX_LISTED])
                        + (" …" if len(unknown) > MAX_LISTED else "")
                        + ". 회의 결정 ref(MTG-…-D1), 기사 id, run_mna_review 결과의 id처럼 받은 결과에 나온 id를 그대로 쓰세요.")
    if not opinion.get("rationale") or any(not c.get("evidence") for c in opinion["rationale"]):
        problems.append("rationale(핵심 판단 근거)마다 근거 id를 하나 이상 붙여야 합니다.")
    if rec == "CONDITIONAL_PROCEED" and not opinion.get("conditions"):
        problems.append("조건부 진행이면 conditions에 조건을 적어야 합니다 (각 조건이 다루는 위험과 함께).")

    unmet = [c for a in analyses for c in a.get("checks") or [] if not c.get("met")]
    missed = [c for c in unmet if c["id"] not in ids]
    if missed:
        problems.append("회의 기준을 충족하지 못한 항목을 판단에서 다루지 않았습니다: "
                        + "; ".join(f"{c['id']} ({c['criterion']}, 평가 {c['actual']})" for c in missed)
                        + ". rationale 또는 conditions에서 이 id를 인용해 어떻게 판단했는지 밝히세요.")

    if opinion.get("evaluation_type") == "reevaluation":
        if prior is None:
            problems.append("재평가는 이전 판단(prior_id)을 lookup_advisory_reviews로 조회해 그 id를 prior_id에 적어야 합니다. "
                            "저장된 이전 판단이 없으면 evaluation_type을 initial로 기록하고 changes_from_prior는 비웁니다 (새 사건은 rationale의 근거로 씁니다).")
        if prior is not None and not (opinion.get("changes_from_prior") or "").strip():
            problems.append("재평가는 changes_from_prior에 이전 판단 대비 달라진 근거 · 수치 · 결론(또는 유지한 이유)을 적어야 합니다.")
    elif opinion.get("prior_id") and prior is None:
        problems.append(f"prior_id '{opinion['prior_id']}'인 저장된 판단을 찾을 수 없습니다.")

    problems += number_problems(opinion, ledger, ids, price)
    return problems



def number_problems(part: dict[str, Any], ledger: Ledger, ids: list[str], price: Any) -> list[str]:
    """Amounts (조 · 억) and percentages written in `part` that no figure of the evidence states."""
    figures = Figures(ledger, ids, extra_amounts=[float(price) * 10000] if isinstance(price, (int, float)) else [])
    bad: list[str] = []
    for where, text in texts(part):
        bad += [f"{where}: '{w}'" for w, v, s in amounts(text) if not figures.has_amount(v, s)]
        bad += [f"{where}: '{w}'" for w, v, s in percents(text) if not figures.has_percent(v, s)]
    if not bad:
        return []
    return ["근거에서 확인되지 않는 수치가 있습니다: " + ", ".join(bad[:MAX_LISTED]) + (" …" if len(bad) > MAX_LISTED else "")
            + ". run_mna_review 결과나 받은 기록의 값을 그대로 쓰고, 새 수치가 필요하면 Tool로 계산하세요 (scenarios)."]


def check_details(details: dict[str, Any], ledger: Ledger, core: dict[str, Any]) -> list[str]:
    """Problems of the second part of a judgement — the three executive views, risks, uncertainties, report paragraphs —
    added to a judgement this run recorded (`core`)."""
    problems: list[str] = []
    analyses = [a for a in ledger.analyses if a.get("found")]
    ids = cited(details)
    unknown = [i for i in ids if ledger.get(i) is None]
    if unknown:
        problems.append("이번 실행의 Tool 결과에 없는 근거 id를 인용했습니다: " + ", ".join(unknown[:MAX_LISTED])
                        + (" …" if len(unknown) > MAX_LISTED else "") + ". 받은 결과에 나온 id를 그대로 쓰세요.")
    views = {p.get("persona"): p for p in details.get("personas") or []}
    missing = [p for p in PERSONAS if p not in views]
    if missing:
        problems.append(f"personas에 {', '.join(missing)} 관점이 없습니다 (CEO · CFO · CTO 각 하나).")
    lenses = {p: {_lens_key(i) for a in analyses for i in (a.get("persona_lenses") or {}).get(p, [])} for p in PERSONAS}
    for p in PERSONAS:
        v = views.get(p)
        if not v:
            continue
        own = [*(v.get("evidence") or []), *(i for c in v.get("key_points") or [] for i in c.get("evidence") or [])]
        if not own:
            problems.append(f"{p} 관점에 근거 id가 없습니다.")
        elif not any(_lens_key(i) in lenses[p] for i in own):
            problems.append(f"{p} 관점이 그 관점의 수치(persona_lenses.{p})를 하나도 인용하지 않았습니다. 같은 근거를 공유하되 그 관점이 먼저 보는 수치로 판단하세요.")
        if not str(v.get("stance") or "").strip():
            problems.append(f"{p} 관점의 stance(입장)가 비어 있습니다.")
    judged = [(views[p].get("judgement") or "").strip() for p in PERSONAS if p in views]
    if len(judged) == 3 and len(set(judged)) < 3:
        problems.append("CEO · CFO · CTO의 judgement가 같은 문장입니다. 관점마다 다른 기준으로 판단하세요.")
    problems += number_problems(details, ledger, list(dict.fromkeys([*ids, *cited(core)])), core.get("price_trillion_krw"))
    return problems

MEETING_KINDS = ("meeting", "meeting_decision", "meeting_issue", "action_item")


def snapshot(opinion: dict[str, Any], ledger: Ledger) -> dict[str, Any]:
    """What is kept with the judgement: the records it cites, every figure of the analyses it rests on, and every meeting
    record the run read (cited or not, it informed the judgement — so it sets the classification and who may read it)."""
    keep = set(cited(opinion)) | {i for i, item in ledger.items.items() if item.get("kind") in MEETING_KINDS}
    analyses = [a for a in ledger.analyses if a.get("found")]
    for a in analyses:
        keep.update(i["id"] for i in a.get("items") or [])
        keep.update(c["id"] for c in a.get("checks") or [])
    items = {i: ledger.items[i] for i in keep if i in ledger.items}
    return {"items": items, "analyses": analyses}


def meeting_codes(evidence: dict[str, Any]) -> list[str]:
    out: set[str] = set()
    for item in evidence["items"].values():
        if item.get("kind") == "meeting":
            out.add(item["id"])
        if item.get("meeting"):
            out.add(item["meeting"])
    return sorted(out)


def evidence_as_of(evidence: dict[str, Any]) -> str | None:
    """The newest date among the cited records and the data the analysis read (the information basis of the judgement)."""
    dates = [str(i["date"])[:10] for i in evidence["items"].values() if i.get("kind") != "calc" and i.get("date")]
    dates += [a["data_as_of"] for a in evidence["analyses"] if a.get("data_as_of")]
    return max(dates) if dates else None
