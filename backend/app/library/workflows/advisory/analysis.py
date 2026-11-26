"""Acquisition analysis — the numbers behind an M&A judgement, computed from the company data (not agent-visible).

    analyze(company, path=…, price_trillion_krw=…, scenarios=[…], criteria=…) →
        facts · calculations (DCF from the cash-flow plan, price against value, customers and contracts,
        technology · IP · people, integration), each with a stable id; scenarios that change the drivers
        (loss of the largest customer's revenue, revenue / margin change, WACC, growth, an IP loss) recomputed
        through the same model; comparisons against internal criteria the caller read from meetings.

It does not recommend: whether to proceed is the Advisory model's judgement over these numbers and the other
evidence of the run (opinion.py checks that judgement against them). Every figure is computed here from the data
file; nothing is a stored conclusion. The company data is synthetic (data_room.source says so).

Money is in 억 원 throughout (prices are stated in 조 원 in the data and shown in both); ratios are percentages.
"""

from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

PERSONAS = ("CEO", "CFO", "CTO")
MAX_SCENARIOS = 4
TRILLION = 10000  # 1조 원 = 10,000억 원


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _load(path: str, mtime: float) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["companies"]


def load_companies(path: Path) -> list[dict[str, Any]]:
    return _load(str(path), path.stat().st_mtime)


def find_company(name: str, path: Path) -> dict[str, Any] | None:
    wanted = (name or "").lower().strip()
    if not wanted:
        return None
    for c in load_companies(path):
        for alias in [c["name"], *c.get("aliases", [])]:
            a = alias.lower()
            if a in wanted or wanted in a:
                return c
    return None


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def won(v: float) -> str:
    """억 원 amount as text: 21,500억 원 (2조 원 이상이면 조 단위를 함께)."""
    text = f"{v:,.0f}억 원"
    if abs(v) >= TRILLION:
        text += f" ({v / TRILLION:.2f}조 원)"
    return text


def trillion(v: float) -> str:
    """억 원 amount stated as a price: 2.8조 원."""
    s = f"{v / TRILLION:.2f}".rstrip("0").rstrip(".")
    return f"{s}조 원"


def pct(v: float) -> str:
    return f"{v:.1f}%"


class _Items:
    """The facts and calculations of one analysis, each with an id the judgement can cite."""

    def __init__(self, aid: str):
        self.aid = aid
        self.items: list[dict[str, Any]] = []

    def add(self, key: str, label: str, value: Any, unit: str | None, *, group: str, kind: str = "calc",
            display: str | None = None, basis: str | None = None, scenario: str | None = None) -> str:
        id_ = f"{self.aid}.{scenario + '.' if scenario else ''}{key}"
        if display is None:
            display = (won(value) if unit == "억 원" else pct(value) if unit == "%" else
                       f"{value:,.1f}배" if unit == "배" else f"{value}{unit or ''}")
        item = {"id": id_, "kind": kind, "group": group, "label": label, "value": value, "unit": unit, "display": display}
        if basis:
            item["basis"] = basis
        if scenario:
            item["scenario"] = scenario
        self.items.append(item)
        return id_


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def _years(c: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(r) for r in c["financials"]["years"]]


def _base_year(c: dict[str, Any]) -> int:
    return int(c["valuation_inputs"]["valuation_date"][:4])


def net_debt(c: dict[str, Any]) -> float:
    b = c.get("balance_sheet", {})
    return b.get("borrowings", 0) + b.get("lease_liabilities", 0) - b.get("cash", 0)


def dcf(rows: list[dict[str, Any]], *, base_year: int, wacc: float, growth: float, debt: float) -> dict[str, float] | None:
    """Equity value from the explicit free cash flows after `base_year` and a Gordon terminal value."""
    flows = [(r["year"], r["fcf"]) for r in rows if r["year"] > base_year]
    if not flows or wacc <= growth:
        return None
    pv = sum(f / (1 + wacc) ** (y - base_year) for y, f in flows)
    last_year, last = flows[-1]
    terminal = last * (1 + growth) / (wacc - growth)
    pv_terminal = terminal / (1 + wacc) ** (last_year - base_year)
    ev = pv + pv_terminal
    return {"pv_explicit": pv, "terminal_value": terminal, "pv_terminal": pv_terminal, "enterprise_value": ev,
            "equity_value": ev - debt, "terminal_share": pv_terminal / ev if ev else 0.0}


def largest_customer(c: dict[str, Any]) -> dict[str, Any] | None:
    customers = c.get("customers") or []
    return max(customers, key=lambda x: x.get("revenue_current_year", 0)) if customers else None


def current_revenue(c: dict[str, Any]) -> float:
    by_year = {r["year"]: r for r in c["financials"]["years"]}
    return by_year[_base_year(c)]["revenue"]


def customer_share(c: dict[str, Any], customer: dict[str, Any], year: int) -> float:
    share = (customer.get("share_forecast") or {}).get(str(year))
    return share if share is not None else customer.get("revenue_current_year", 0) / current_revenue(c)


def apply_scenario(c: dict[str, Any], rows: list[dict[str, Any]], s: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """The cash-flow plan with a scenario's driver changes; also the assumptions it made, in words."""
    rows = deepcopy(rows)
    vi = c["valuation_inputs"]
    cm = s.get("contribution_margin") if s.get("contribution_margin") is not None else vi.get("contribution_margin", 0.25)
    first_forecast = _base_year(c) + 1
    start = max(int(s.get("from_year") or first_forecast), first_forecast)
    loss = s.get("largest_customer_revenue_loss") or 0.0
    revenue_change = s.get("revenue_change") or 0.0
    margin_change = s.get("operating_margin_change") or 0.0
    big = largest_customer(c)
    notes: list[str] = []
    if loss and big:
        notes.append(f"{big['name']} 매출의 {loss * 100:.0f}%를 {start}년부터 잃음 (연도별 매출 비중 전망 적용)")
    if revenue_change:
        notes.append(f"{start}년부터 그 밖의 매출 전망 {revenue_change * 100:+.0f}%")
    if loss or revenue_change:
        notes.append(f"매출 변화의 영업이익 · 현금흐름 영향 = 매출 변화 × 공헌이익률 {cm * 100:.0f}% (세전 근사, 법인세 효과 미반영)")
    if margin_change:
        notes.append(f"{start}년부터 영업이익률 {margin_change * 100:+.1f}%p (같은 금액만큼 현금흐름 변화)")
    for r in rows:
        if r["year"] < start:
            continue
        lost = r["revenue"] * customer_share(c, big, r["year"]) * loss if (loss and big) else 0.0
        delta = -lost + (r["revenue"] - lost) * revenue_change
        r["revenue"] += delta
        r["operating_profit"] += delta * cm + r["revenue"] * margin_change
        r["fcf"] += delta * cm + r["revenue"] * margin_change
        r["lost_customer_revenue"] = lost
    return rows, notes


def _break_even(rows: list[dict[str, Any]], after: int) -> int | None:
    return next((r["year"] for r in rows if r["year"] > after and r["operating_profit"] > 0), None)


def _funding_need(rows: list[dict[str, Any]], from_year: int) -> float:
    """Cumulative free cash outflow from `from_year` until the first positive year."""
    total = 0.0
    for r in rows:
        if r["year"] < from_year:
            continue
        if r["fcf"] >= 0:
            break
        total += r["fcf"]
    return total


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def _price_label(c: dict[str, Any], price: float) -> str:
    deal = c.get("deal", {})
    if abs(price - deal.get("asking_trillion_krw", -1)) < 1e-9:
        return "최초 요구가"
    if abs(price - deal.get("proposal_trillion_krw", -1)) < 1e-9:
        return "제안가"
    return "검토 가정 가격"


def _valuation_items(it: _Items, c: dict[str, Any], rows: list[dict[str, Any]], *, price: float, wacc: float,
                     growth: float, scenario: str | None = None, ip_loss: float = 0.0) -> dict[str, Any]:
    base_year = _base_year(c)
    debt = net_debt(c)
    v = dcf(rows, base_year=base_year, wacc=wacc, growth=growth, debt=debt)
    out: dict[str, Any] = {"dcf": v}
    if v is None:
        return out
    equity = v["equity_value"] - ip_loss
    out["equity"] = equity
    tag = " (시나리오)" if scenario else ""
    it.add("dcf.enterprise_value", f"DCF 기업가치{tag}", round(v["enterprise_value"], 1), "억 원", group="valuation",
           basis=f"WACC {wacc * 100:.1f}% · 영구성장률 {growth * 100:.1f}% · {base_year + 1}~{rows[-1]['year']}년 FCF", scenario=scenario)
    if ip_loss:
        it.add("dcf.ip_loss", f"반영한 특허 분쟁 손실 가정{tag}", round(ip_loss, 1), "억 원", group="valuation", scenario=scenario)
    it.add("dcf.equity_value", f"DCF 주주가치{tag}", round(equity, 1), "억 원", group="valuation",
           basis=f"기업가치 − 순차입금 {won(debt)}" + (" − 특허 분쟁 손실 가정" if ip_loss else ""), scenario=scenario)
    it.add("dcf.terminal_share", f"기업가치 중 잔존가치 비중{tag}", round(v["terminal_share"] * 100, 1), "%", group="valuation",
           basis=f"{rows[-1]['year']}년 이후 현금흐름", scenario=scenario)
    p = price * TRILLION
    if equity > 0:
        it.add("price.premium_vs_dcf", f"가격의 DCF 주주가치 대비 프리미엄{tag}", round((p / equity - 1) * 100, 1), "%",
               group="valuation", basis=f"{trillion(p)} ÷ {won(equity)} − 1", scenario=scenario)
    it.add("price.gap_vs_dcf", f"가격 − DCF 주주가치{tag}", round(p - equity, 1), "억 원", group="valuation", scenario=scenario)
    return out


def _plan_items(it: _Items, c: dict[str, Any], rows: list[dict[str, Any]], scenario: str | None = None) -> None:
    base_year = _base_year(c)
    tag = " (시나리오)" if scenario else ""
    nxt = next((r for r in rows if r["year"] == base_year + 1), None)
    last = rows[-1]
    if nxt:
        it.add("plan.revenue_next", f"{nxt['year']}년 매출 전망{tag}", round(nxt["revenue"], 1), "억 원", group="financials", scenario=scenario)
    it.add("plan.revenue_last", f"{last['year']}년 매출 전망{tag}", round(last["revenue"], 1), "억 원", group="financials", scenario=scenario)
    it.add("plan.operating_profit_last", f"{last['year']}년 영업이익 전망{tag}", round(last["operating_profit"], 1), "억 원",
           group="financials", scenario=scenario)
    it.add("plan.fcf_last", f"{last['year']}년 자유현금흐름 전망{tag}", round(last["fcf"], 1), "억 원", group="financials", scenario=scenario)
    current = next((r for r in rows if r["year"] == base_year), None)
    if current and current["operating_profit"] > 0 and all(r["operating_profit"] > 0 for r in rows if r["year"] > base_year):
        it.add("plan.break_even_year", f"영업이익{tag}", "흑자 유지", None, group="financials",
               display=f"{base_year}년부터 전망 기간 내내 흑자", scenario=scenario)
    else:
        be = _break_even(rows, base_year)
        it.add("plan.break_even_year", f"영업흑자 전환 연도{tag}", be if be else "전망 기간 내 없음", "년" if be else None,
               group="financials", display=f"{be}년" if be else "전망 기간 내 없음", scenario=scenario)
    need = _funding_need(rows, base_year)
    it.add("plan.funding_need", f"{base_year}년부터 현금흐름 흑자 전까지 누적 자금 소요{tag}", round(need, 1), "억 원",
           group="financials", scenario=scenario)


def analyze(
    company: str,
    *,
    path: Path,
    query: str = "",
    price_trillion_krw: float | None = None,
    scenarios: list[dict[str, Any]] | None = None,
    criteria: dict[str, Any] | None = None,
    analysis_id: str = "A1",
) -> dict[str, Any]:
    c = find_company(company, path)
    if c is None:
        return {"query": query, "company": company, "found": False, "message": f"'{company}'에 대한 기업 데이터가 없습니다."}
    if "financials" not in c or "valuation_inputs" not in c:
        return {"query": query, "company": c["name"], "found": False, "message": f"'{c['name']}'의 재무 · 가치평가 데이터가 없습니다."}
    scenarios = list(scenarios or [])
    if len(scenarios) > MAX_SCENARIOS:
        return {"error": f"시나리오는 한 번에 {MAX_SCENARIOS}개까지입니다.", "found": True, "company": c["name"]}
    for s in scenarios:
        loss = s.get("largest_customer_revenue_loss")
        if loss is not None and not 0 <= loss <= 1:
            return {"error": "largest_customer_revenue_loss는 0~1 사이의 비율입니다 (예: 절반이면 0.5).", "found": True, "company": c["name"]}
        if (s.get("wacc") or 0.2) <= (s.get("terminal_growth") or 0) or (s.get("wacc") is not None and not 0 < s["wacc"] < 0.5):
            return {"error": "wacc는 0~0.5 사이이고 terminal_growth보다 커야 합니다.", "found": True, "company": c["name"]}

    deal, vi = c.get("deal", {}), c["valuation_inputs"]
    price = price_trillion_krw if price_trillion_krw is not None else deal.get("asking_trillion_krw")
    if price is None or price <= 0:
        return {"error": "평가할 인수가격이 없습니다 (price_trillion_krw).", "found": True, "company": c["name"]}
    criteria = criteria or {}
    base_year = _base_year(c)
    rows = _years(c)
    by_year = {r["year"]: r for r in rows}
    cur = by_year[base_year]
    it = _Items(analysis_id)

    # Deal and price
    it.add("deal.price", f"평가 가격 ({_price_label(c, price)})", price * TRILLION, "억 원", group="deal", kind="fact",
           display=trillion(price * TRILLION))
    if deal.get("asking_trillion_krw"):
        it.add("deal.asking", "최초 인수 요구가", deal["asking_trillion_krw"] * TRILLION, "억 원", group="deal", kind="fact",
               display=trillion(deal["asking_trillion_krw"] * TRILLION), basis=deal.get("asking_note"))
    if deal.get("proposal_trillion_krw"):
        it.add("deal.proposal", "가격 조정 제안가", deal["proposal_trillion_krw"] * TRILLION, "억 원", group="deal", kind="fact",
               display=trillion(deal["proposal_trillion_krw"] * TRILLION), basis=deal.get("proposal_note"))

    # Current year
    first = rows[0]
    span = base_year - first["year"]
    it.add("fin.revenue_current", f"{base_year}년 매출 ({cur['kind']})", cur["revenue"], "억 원", group="financials", kind="fact")
    it.add("fin.operating_profit_current", f"{base_year}년 영업이익", cur["operating_profit"], "억 원", group="financials", kind="fact")
    it.add("fin.operating_margin_current", f"{base_year}년 영업이익률", round(cur["operating_profit"] / cur["revenue"] * 100, 1), "%",
           group="financials")
    if span > 0:
        it.add("fin.revenue_cagr", f"{first['year']}~{base_year}년 매출 연평균 성장률",
               round(((cur["revenue"] / first["revenue"]) ** (1 / span) - 1) * 100, 1), "%", group="financials")
    cy = c["financials"].get("current_year") or {}
    if "depreciation_amortization" in cy:
        ebitda = cur["operating_profit"] + cy["depreciation_amortization"]
        it.add("fin.ebitda", f"{base_year}년 EBITDA", ebitda, "억 원", group="financials", basis="영업이익 + 감가상각 · 무형자산상각")
        one_off = sum(x["amount"] for x in cy.get("one_off_items", []))
        if one_off:
            it.add("fin.adjusted_ebitda", f"{base_year}년 조정 EBITDA", ebitda + one_off, "억 원", group="financials",
                   basis="EBITDA + " + " · ".join(f"{x['item']} {x['amount']:,}억" for x in cy["one_off_items"]))
    _plan_items(it, c, rows)

    # Valuation (base)
    wacc, growth = vi["wacc"], vi["terminal_growth"]
    it.add("val.net_debt", "순차입금", net_debt(c), "억 원", group="valuation", basis="차입금 + 리스부채 − 현금")
    base = _valuation_items(it, c, rows, price=price, wacc=wacc, growth=growth)
    grid = []
    for w in vi.get("wacc_range", [wacc]):
        row = []
        for g in vi.get("terminal_growth_range", [growth]):
            v = dcf(rows, base_year=base_year, wacc=w, growth=g, debt=net_debt(c))
            row.append(round(v["equity_value"], 1) if v else None)
        grid.append({"wacc": w, "values": row})
    flat = [x for r in grid for x in r["values"] if x is not None]
    if flat:
        it.add("val.sensitivity_low", "주주가치 민감도 하단", min(flat), "억 원", group="valuation",
               basis="WACC × 영구성장률 범위의 최솟값")
        it.add("val.sensitivity_high", "주주가치 민감도 상단", max(flat), "억 원", group="valuation",
               basis="WACC × 영구성장률 범위의 최댓값")
    it.add("val.ev_to_revenue", f"가격 기준 EV / {base_year}년 매출", round((price * TRILLION + net_debt(c)) / cur["revenue"], 1), "배",
           group="valuation")

    # Working capital
    bs = c.get("balance_sheet", {})
    if "working_capital_target" in bs:
        shortfall = bs["working_capital_target"] - bs["working_capital_expected_at_close"]
        it.add("wc.shortfall", "정상운전자본 부족 예상액", shortfall, "억 원", group="working_capital",
               basis=f"목표 {bs['working_capital_target']:,}억 − 종결 시 예상 {bs['working_capital_expected_at_close']:,}억")
        it.add("wc.price_after_adjustment", "운전자본 부족 반영 가격", price * TRILLION - shortfall, "억 원", group="working_capital")
        for key, label in (("overdue_receivables_90d", "90일 초과 연체 매출채권"), ("aged_inventory_180d", "180일 초과 장기 재고")):
            if key in bs:
                it.add(f"wc.{key}", label, bs[key], "억 원", group="working_capital", kind="fact")

    # Customers and contracts
    customers = c.get("customers") or []
    big = largest_customer(c)
    if big:
        it.add("cust.largest_revenue", f"최대 고객 {big['name']} 매출 ({base_year}년)", big["revenue_current_year"], "억 원",
               group="customers", kind="fact")
        it.add("cust.largest_share", f"최대 고객 {big['name']} 매출 비중", round(big["revenue_current_year"] / cur["revenue"] * 100, 1), "%",
               group="customers")
        top3 = sum(sorted((x["revenue_current_year"] for x in customers), reverse=True)[:3])
        it.add("cust.top3_share", "상위 3개 고객 매출 비중", round(top3 / cur["revenue"] * 100, 1), "%", group="customers")
        po = [x.get("contract", {}).get("confirmed_po_next_year") for x in customers]
        if all(p is not None for p in po) and base_year + 1 in by_year:
            total_po = sum(po)
            it.add("cust.po_next_year", f"{base_year + 1}년 확정 발주(PO)", total_po, "억 원", group="customers", kind="fact")
            it.add("cust.po_coverage", f"{base_year + 1}년 매출 계획 대비 확정 발주 비율",
                   round(total_po / by_year[base_year + 1]["revenue"] * 100, 1), "%", group="customers")
            if big.get("contract", {}).get("confirmed_po_next_year") is not None:
                it.add("cust.largest_po", f"{big['name']} {base_year + 1}년 확정 발주", big["contract"]["confirmed_po_next_year"], "억 원",
                       group="customers", kind="fact")
    dw = c.get("design_wins_current_year")
    if dw:
        it.add("cust.design_wins", f"{base_year}년 설계 채택", dw["total"], "건", group="customers", kind="fact",
               display=f"{dw['total']}건 (양산 {dw['mass_production']} · 시험·인증 {dw['qualification']} · 지연 {dw['delayed']})")

    # Technology, IP, people, integration
    t = c.get("technology", {})
    if t.get("time_to_market_gain_months"):
        lo, hi = t["time_to_market_gain_months"]
        it.add("tech.ttm_gain", "자체 개발 대비 Time-to-Market 단축", lo, "개월", group="technology", kind="fact",
               display=f"{lo}~{hi}개월",
               basis=f"자체 개발 {t['acquirer_internal_development_months'][0]}~{t['acquirer_internal_development_months'][1]}개월 vs "
                     f"인수 후 통합 {t['post_acquisition_integration_months'][0]}~{t['post_acquisition_integration_months'][1]}개월")
    perf = t.get("performance")
    if perf:
        adv = (perf["tops_per_watt"] / perf["benchmark_tops_per_watt"] - 1) * 100
        it.add("tech.performance_advantage", "전력효율 우위 (경쟁 시료 대비)", round(adv, 1), "%", group="technology",
               basis=f"{perf['tops_per_watt']} vs {perf['benchmark_tops_per_watt']} TOPS/W"
                     + ("" if perf.get("independently_verified") else " — 독립 검증 전"))
    if "prototype_yield" in t:
        it.add("tech.yield", "시제품 수율 / 양산 목표", round(t["prototype_yield"] * 100, 1), "%", group="technology", kind="fact",
               display=f"{t['prototype_yield'] * 100:.0f}% / 목표 {t.get('mass_production_target_yield', 0) * 100:.0f}%")
    pat = t.get("patents")
    if pat:
        it.add("tech.core_patents_confirmed", "권리 귀속이 확인된 핵심 특허군", pat["core_families_title_confirmed"], "개", group="technology",
               kind="fact", display=f"{pat['core_families']}개 중 {pat['core_families_title_confirmed']}개 (등록특허 {pat['registered']}건)")
    disputes = t.get("ip_disputes") or []
    it.add("tech.ip_disputes", "진행 중인 특허 분쟁", len(disputes), "건", group="technology", kind="fact",
           display=f"{len(disputes)}건" + (" — " + "; ".join(f"{d['counterparty']}: {d['subject']} ({d['status']})" for d in disputes) if disputes else ""))
    if t.get("ip_exposure_range"):
        lo, hi = t["ip_exposure_range"]
        it.add("tech.ip_exposure_low", "특허 분쟁 대응 비용 범위 하단", lo, "억 원", group="technology", kind="fact", basis=t.get("ip_exposure_note"))
        it.add("tech.ip_exposure_high", "특허 분쟁 대응 비용 범위 상단", hi, "억 원", group="technology", kind="fact", basis=t.get("ip_exposure_note"))
        it.add("tech.ip_exposure_high_vs_price", "분쟁 비용 상단의 가격 대비 비율", round(hi / (price * TRILLION) * 100, 1), "%", group="technology")
    ppl = c.get("people", {})
    if "key_designers" in ppl:
        it.add("people.retention_open", "잔류 미합의 핵심 설계자", ppl["retention_open"], "명", group="people", kind="fact",
               display=f"핵심 설계자 {ppl['key_designers']}명 중 {ppl['retention_open']}명 미합의 (합의 초안 {ppl['retention_draft_agreed']}명)",
               basis=ppl.get("retention_note"))
    integ = c.get("integration", {})
    if "cost_24m" in integ:
        it.add("integ.cost", "인수 후 24개월 통합비용", integ["cost_24m"], "억 원", group="integration", kind="fact",
               basis=" · ".join(f"{k} {v:,}" for k, v in integ.get("cost_breakdown", {}).items()))
    if integ.get("synergy_annual_from_2029"):
        it.add("integ.synergy", "연간 비용 절감 시너지 가정 (2029년부터)", integ["synergy_annual_from_2029"], "억 원", group="integration",
               kind="fact", basis=integ.get("synergy_note"))
    st = c.get("strategic", {})
    if st.get("fit"):
        it.add("strategy.fit", "전략적 적합성", st["fit"], None, group="strategy", kind="fact", display=st["fit"])
    if st.get("market"):
        it.add("strategy.market", "시장 상황", st["market"], None, group="strategy", kind="fact", display=st["market"])

    # Internal criteria the caller read from meetings
    checks = []
    ceiling = criteria.get("price_ceiling_trillion_krw")
    if ceiling:
        checks.append({"id": it.add("check.price_ceiling", "회의 기준 가격 상한 대비 평가 가격", price * TRILLION, "억 원", group="checks",
                                    display=f"{trillion(price * TRILLION)} (기준 {trillion(ceiling * TRILLION)})"),
                       "criterion": f"가격 {trillion(ceiling * TRILLION)} 이하", "actual": trillion(price * TRILLION),
                       "met": price <= ceiling})
    need_months = criteria.get("min_time_to_market_gain_months")
    if need_months and t.get("time_to_market_gain_months"):
        lo = t["time_to_market_gain_months"][0]
        checks.append({"id": it.add("check.time_to_market", "회의 기준 Time-to-Market 단축 대비", lo, "개월", group="checks",
                                    display=f"최소 {lo}개월 (기준 {need_months}개월)"),
                       "criterion": f"Time-to-Market {need_months}개월 이상 단축", "actual": f"{lo}~{t['time_to_market_gain_months'][1]}개월",
                       "met": lo >= need_months})

    # Scenarios: the same model with changed drivers
    scenario_out = []
    for n, s in enumerate(scenarios, start=1):
        tag = f"S{n}"
        srows, notes = apply_scenario(c, rows, s)
        s_price = s.get("price_trillion_krw") or price
        _plan_items(it, c, srows, scenario=tag)
        ip_loss = s.get("ip_loss") or 0.0
        sv = _valuation_items(it, c, srows, price=s_price, wacc=s.get("wacc") or wacc, growth=s.get("terminal_growth") if s.get("terminal_growth") is not None else growth,
                              scenario=tag, ip_loss=ip_loss)
        if s.get("price_trillion_krw"):
            it.add("deal.price", f"평가 가격 (시나리오)", s_price * TRILLION, "억 원", group="deal", display=trillion(s_price * TRILLION), scenario=tag)
        if s.get("wacc") or s.get("terminal_growth") is not None:
            notes.append(f"WACC {(s.get('wacc') or wacc) * 100:.1f}% · 영구성장률 {(s.get('terminal_growth') if s.get('terminal_growth') is not None else growth) * 100:.1f}%")
        if ip_loss:
            notes.append(f"특허 분쟁 손실 {won(ip_loss)}를 주주가치에서 차감")
        if s_price != price:
            notes.append(f"평가 가격 {trillion(s_price * TRILLION)}")
        changes = []
        if base.get("equity") is not None and sv.get("equity") is not None:
            d = sv["equity"] - base["equity"]
            changes.append({"id": it.add("delta.equity_value", "기준 대비 주주가치 변화", round(d, 1), "억 원", group="valuation", scenario=tag,
                                         display=f"{d:+,.0f}억 원 ({d / base['equity'] * 100:+.1f}%)"),
                            "label": "DCF 주주가치", "base": won(base["equity"]), "scenario": won(sv["equity"])})
            it.add("delta.equity_value_pct", "기준 대비 주주가치 변화율", round(d / base["equity"] * 100, 1), "%", group="valuation", scenario=tag)
        loss = s.get("largest_customer_revenue_loss") or 0.0
        if loss and big and big.get("contract", {}).get("confirmed_po_next_year") is not None and base_year + 1 in by_year:
            total_po = sum(x.get("contract", {}).get("confirmed_po_next_year") or 0 for x in customers) - big["contract"]["confirmed_po_next_year"] * loss
            nxt = next(r for r in srows if r["year"] == base_year + 1)
            changes.append({"id": it.add("cust.po_coverage", f"{base_year + 1}년 매출 계획 대비 확정 발주 비율 (시나리오)",
                                         round(total_po / by_year[base_year + 1]["revenue"] * 100, 1), "%", group="customers", scenario=tag,
                                         basis=f"{big['name']} 발주의 {loss * 100:.0f}% 취소 가정, 기존 매출 계획 대비"),
                            "label": "확정 발주 비율", "base": None, "scenario": None})
            it.add("cust.lost_revenue_next", f"{base_year + 1}년 잃는 {big['name']} 매출 (시나리오)", round(nxt.get("lost_customer_revenue", 0.0), 1),
                   "억 원", group="customers", scenario=tag)
        scenario_out.append({"id": tag, "name": s.get("name") or tag, "assumptions": {k: v for k, v in s.items() if v is not None},
                             "notes": notes, "plan": [_row(r) for r in srows], "changes": changes})

    lenses = _lenses(it)
    return {
        "query": query,
        "company": c["name"],
        "company_id": c.get("id"),
        "found": True,
        "analysis_id": analysis_id,
        "synthetic": True,
        "data_source": c.get("data_room", {}).get("source"),
        "data_as_of": c.get("data_room", {}).get("as_of"),
        "business": c.get("business"),
        "acquirer": deal.get("acquirer") or st.get("acquirer"),
        "price": {"trillion_krw": price, "label": _price_label(c, price), "asking_trillion_krw": deal.get("asking_trillion_krw"),
                  "proposal_trillion_krw": deal.get("proposal_trillion_krw")},
        "assumptions": {"wacc": wacc, "terminal_growth": growth, "contribution_margin": vi.get("contribution_margin"),
                        "valuation_date": vi["valuation_date"], "forecast_premise": c["financials"].get("forecast_premise")},
        "items": it.items,
        "checks": checks,
        "scenarios": scenario_out,
        "tables": {
            "plan": [_row(r) for r in rows],
            "sensitivity": {"terminal_growth": vi.get("terminal_growth_range", [growth]), "rows": grid},
            "customers": [{"name": x["name"], "revenue": x["revenue_current_year"],
                           "share": round(x["revenue_current_year"] / cur["revenue"] * 100, 1),
                           "expires": x.get("contract", {}).get("expires"), "terms": x.get("contract", {}).get("terms"),
                           "po_next_year": x.get("contract", {}).get("confirmed_po_next_year")} for x in customers],
            "ip_disputes": disputes,
            "integration_cost": integ.get("cost_breakdown"),
        },
        "persona_lenses": lenses,
        "open_items": open_items(c),
        "note": "권고는 포함하지 않는다: 이 수치와 회의 · 뉴스 근거를 종합한 판단은 경영자문 에이전트가 내린다. 기업 데이터는 합성 데이터다.",
    }


def _row(r: dict[str, Any]) -> dict[str, Any]:
    out = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in r.items()}
    out["operating_margin"] = round(r["operating_profit"] / r["revenue"] * 100, 1) if r["revenue"] else None
    return out


_LENS_GROUPS = {
    "CEO": ("deal", "strategy", "customers", "checks"),
    "CFO": ("deal", "financials", "valuation", "working_capital", "checks"),
    "CTO": ("technology", "people", "integration", "checks"),
}
_LENS_EXTRA = {"CEO": ("tech.ttm_gain", "plan.break_even_year", "price.premium_vs_dcf", "integ.synergy"),
               "CFO": ("cust.largest_share", "cust.po_coverage", "tech.ip_exposure_high", "tech.ip_exposure_high_vs_price", "integ.cost"),
               "CTO": ("cust.design_wins",)}


def _lenses(it: _Items) -> dict[str, list[str]]:
    """Which figures each executive perspective weighs first (the same evidence, a different focus)."""
    out: dict[str, list[str]] = {}
    for persona in PERSONAS:
        out[persona] = [i["id"] for i in it.items
                        if i["group"] in _LENS_GROUPS[persona] or any(i["id"].endswith("." + k) for k in _LENS_EXTRA[persona])]
    return out


def open_items(c: dict[str, Any]) -> list[str]:
    """What the data itself marks as not yet confirmed (contract consents, unverified performance, title, disputes, retention)."""
    out = []
    for x in c.get("customers") or []:
        coc = x.get("contract", {}).get("change_of_control")
        if coc and "확인되지 않" in coc:
            out.append(f"{x['name']} 계약의 변경지배권 동의 — {coc}")
    t = c.get("technology", {})
    if t.get("performance") and not t["performance"].get("independently_verified"):
        out.append(f"NPU 성능 우위 — {t['performance'].get('note', '독립 검증 전')}")
    pat = t.get("patents")
    if pat and pat["core_families_title_confirmed"] < pat["core_families"]:
        out.append(f"핵심 특허군 {pat['core_families'] - pat['core_families_title_confirmed']}개의 권리 귀속 (공동개발 · 사용권 계약 검토 필요)")
    for d in t.get("ip_disputes") or []:
        out.append(f"특허 분쟁 {d['counterparty']} — {d['subject']}: {d['status']}")
    ppl = c.get("people", {})
    if ppl.get("retention_open"):
        out.append(f"핵심 설계자 {ppl['retention_open']}명 잔류 미합의 — {ppl.get('retention_note', '')}".rstrip(" —"))
    for x in (c.get("financials", {}).get("current_year") or {}).get("one_off_items", []):
        out.append(f"조정 항목 검증: {x['item']} {x['amount']:,}억 원")
    integ = c.get("integration", {})
    if integ.get("synergy_annual_from_2029") and not integ.get("synergy_in_dcf"):
        out.append("비용 절감 시너지는 DCF에 반영되지 않은 가정 (계약 · 실적 확인 전)")
    return out


def for_model(result: dict[str, Any]) -> dict[str, Any]:
    """What the model reads of an analysis: every figure as one line "id — label: value (basis)", grouped; the scenarios'
    assumptions; the criteria checks; which groups each perspective weighs first. The full result (yearly tables, …) stays
    in the run's evidence ledger for the judgement record and the report."""
    if not result.get("found") or result.get("error"):
        return result
    groups: dict[str, list[str]] = {}
    for i in result["items"]:
        line = f"{i['id']} — {i['label']}: {i['display']}"
        if i.get("basis") and i["kind"] == "calc":
            line += f" ({i['basis']})"
        groups.setdefault(i["group"], []).append(line)
    return {
        "found": True, "company": result["company"], "analysis_id": result["analysis_id"], "price": result["price"],
        "data_as_of": result["data_as_of"], "data_source": result["data_source"], "assumptions": result["assumptions"],
        "figures": groups,
        "checks": [{"id": c["id"], "criterion": c["criterion"], "actual": c["actual"], "met": c["met"]} for c in result["checks"]],
        "scenarios": [{"id": s["id"], "name": s["name"], "notes": s["notes"]} for s in result["scenarios"]],
        "persona_lenses": {p: {"groups": [g for g in _LENS_GROUPS[p] if g in groups],
                               "also": [i for i in ids if any(i.endswith("." + k) for k in _LENS_EXTRA[p])]}
                           for p, ids in result["persona_lenses"].items()},
        "open_items": result["open_items"],
        "note": result["note"] + " persona_lenses: 그 관점이 먼저 볼 figures 묶음(groups)과 그 밖의 수치 id(also).",
    }
