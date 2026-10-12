"""M&A review — implementation workflow behind the run_mna_review Tool
(app/capabilities/tools/).

    입력 정보 정리 → persona별 평가 기준 적용 → 항목별 평가 → 긍정 / 우려 요인 → recommendation → conditions

Reads the company fixture, evaluates a fixed catalogue of criteria against
it (deterministic rules), and reports them through the lens of one executive
persona. The persona is a perspective, not an agent: every criterion is
evaluated for every persona, but which criteria lead the report, in what
order, and which conditions come first differ per persona (PERSONAS).
The recommendation itself is computed over all criteria so the same data
yields the same verdict with a different rationale.

`context` is free text the caller forwards from its own conversation (e.g.
criteria decided in past meetings, risks confirmed by news). It is scanned
for a few signals — an internal price ceiling, a Time-to-Market requirement,
risk topics that were externally confirmed — which sharpen the evidence of
the matching criteria. No fixed format is expected.

This module is not agent-visible and has no LangChain dependency. A real
implementation (financial model, valuation service, due-diligence data)
replaces the evaluators while keeping the input/output contract.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "companies.json"

Assessment = dict[str, Any]

# ---------------------------------------------------------------------------
# Personas: which criteria matter, in priority order
# ---------------------------------------------------------------------------

PERSONAS: dict[str, dict[str, Any]] = {
    "CEO": {
        "label": "CEO 관점",
        "aliases": ("ceo", "최고경영자", "대표"),
        "criteria": ["strategic_fit", "time_to_market", "risk_opportunity_balance", "business_value"],
    },
    "CFO": {
        "label": "CFO 관점",
        "aliases": ("cfo", "최고재무책임자", "재무"),
        "criteria": ["valuation", "profitability", "customer_concentration", "financial_downside", "deal_terms"],
    },
    "CTO": {
        "label": "CTO 관점",
        "aliases": ("cto", "최고기술책임자", "기술"),
        "criteria": ["technology_fit", "time_to_market", "key_talent_retention", "ip_risk", "integration"],
    },
}

RECOMMENDATION_LABELS = {
    "PROCEED": "진행",
    "CONDITIONAL_PROCEED": "조건부 진행",
    "DO_NOT_PROCEED": "보류",
}

_LEVEL_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

# Thresholds of the deterministic rules.
_HIGH_PREMIUM = 0.15  # asking price this far above fair value is a HIGH concern
_BLOCKING_PREMIUM = 0.50  # ... and this far above is a DO_NOT_PROCEED
_HIGH_CUSTOMER_SHARE = 0.40

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_companies(path: Path = DATA_PATH) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)["companies"]


def find_company(name: str, path: Path = DATA_PATH) -> dict[str, Any] | None:
    wanted = name.lower().strip()
    if not wanted:
        return None
    for c in load_companies(path):
        for alias in c["aliases"]:
            a = alias.lower()
            if a in wanted or wanted in a:
                return c
    return None


def resolve_persona(persona: str) -> str | None:
    text = (persona or "").lower().strip()
    for key, spec in PERSONAS.items():
        if text == key.lower() or any(a in text for a in spec["aliases"]):
            return key
    return None


# ---------------------------------------------------------------------------
# Context signals
# ---------------------------------------------------------------------------

_FLAG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "customer_concentration": ("알파모바일", "alphamobile", "다변화", "고객 집중", "최대 고객", "의존도"),
    "legal_dispute": ("소송", "특허", "코어npu", "corenpu"),
    "key_personnel": ("cto", "핵심인력", "핵심 인력", "핵심 설계", "이탈", "retention", "근속"),
}
_CEILING_WORDS = ("초과", "이하", "상한", "기준", "넘")


def extract_context_signals(context: str | None) -> dict[str, Any]:
    """Signals the evaluators can use. Everything is optional."""
    text = context or ""
    lower = text.lower()

    ceiling: float | None = None
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*조", text):
        window = text[m.end() : m.end() + 14]
        if any(w in window for w in _CEILING_WORDS):
            ceiling = float(m.group(1))
            break

    ttm_years: int | None = None
    if any(k in lower for k in ("time-to-market", "시장 진입", "단축", "ttm")):
        m = re.search(r"(\d+)\s*년\s*이상", text)
        if m:
            ttm_years = int(m.group(1))

    flags = [flag for flag, keys in _FLAG_KEYWORDS.items() if any(k in lower for k in keys)]
    return {"price_ceiling_trillion_krw": ceiling, "ttm_requirement_years": ttm_years, "confirmed_risks": flags}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _pct(x: float) -> str:
    s = f"{x * 100:.1f}".rstrip("0").rstrip(".")
    return f"{s}%"


def _trillion(x: float) -> str:
    return f"{x:.1f}조 원"


def _months(r: list[int]) -> str:
    return f"{r[0]}~{r[1]}개월"


def summarize_inputs(c: dict[str, Any]) -> dict[str, Any]:
    """입력 정보 정리 — the facts the review is based on, as display-ready labels."""
    fin, val, cust, tech = c["financials"], c["valuation"], c["customers"]["largest"], c["technology"]
    return {
        "company": c["name"],
        "business": c["business"],
        "financials": {
            "fiscal_year": fin["fiscal_year"],
            "revenue": fin["revenue_label"],
            "revenue_cagr_3y": _pct(fin["revenue_cagr_3y"]),
            "operating_margin": _pct(fin["operating_margin"]),
        },
        "valuation": {
            "asking": _trillion(val["asking_trillion_krw"]),
            "fair_value": f"{val['fair_value_low_trillion_krw']:.1f}~{_trillion(val['fair_value_high_trillion_krw'])}",
        },
        "customers": {"largest": cust["name"], "largest_share": _pct(cust["revenue_share"])},
        "technology": {
            "domain": tech["domain"],
            "internal_development": _months(tech["acquirer_internal_development_months"]),
            "integration_after_acquisition": _months(tech["post_acquisition_integration_months"]),
            "time_to_market_gain": _months(tech["time_to_market_gain_months"]),
            "core_team": tech["core_team"],
            "ip_dispute": f"{tech['ip_dispute']['counterparty']} · {tech['ip_dispute']['subject']} ({tech['ip_dispute']['status']})",
        },
    }


# ---------------------------------------------------------------------------
# Criteria evaluators (deterministic rules over fixture + context signals)
# ---------------------------------------------------------------------------


def _a(
    criterion: str,
    label: str,
    verdict: str,
    finding: str,
    evidence: list[str],
    *,
    level: str | None = None,
    condition: str | None = None,
) -> Assessment:
    return {
        "criterion": criterion,
        "label": label,
        "verdict": verdict,  # positive | concern | neutral
        "level": level,  # HIGH | MEDIUM | LOW for concerns
        "finding": finding,
        "evidence": evidence,
        "condition": condition,  # what must hold for the deal to proceed
    }


def _strategic_fit(c: dict, s: dict) -> Assessment:
    st = c["strategic"]
    return _a(
        "strategic_fit",
        "전략적 적합성",
        "positive",
        f"{c['business']} 역량이 {st['acquirer']} 전략과 부합",
        [st["fit"], st["market"]],
    )


def _time_to_market(c: dict, s: dict) -> Assessment:
    t = c["technology"]
    gain = t["time_to_market_gain_months"]
    evidence = [
        f"자체 개발 {_months(t['acquirer_internal_development_months'])} vs 인수 후 통합 {_months(t['post_acquisition_integration_months'])}",
        f"기대 Time-to-Market 단축 {_months(gain)}",
    ]
    req = s.get("ttm_requirement_years")
    if req is not None:
        met = gain[0] >= req * 12
        evidence.append(f"요청 맥락의 내부 기준(최소 {req}년 단축) {'충족' if met else '미충족'}")
    return _a("time_to_market", "자체 개발 대비 Time-to-Market", "positive", f"자체 개발 대비 약 {_months(gain)} 빠른 시장 진입", evidence)


def _business_value(c: dict, s: dict) -> Assessment:
    f = c["financials"]
    return _a(
        "business_value",
        "사업 추진 가치",
        "positive",
        f"매출 {f['revenue_label']}, 3년 CAGR {_pct(f['revenue_cagr_3y'])}의 고성장 사업",
        [f"{f['fiscal_year']}년 매출 {f['revenue_label']}", f"3년 CAGR {_pct(f['revenue_cagr_3y'])}", f"영업이익률 {_pct(f['operating_margin'])} (아직 적자)"],
    )


def _valuation(c: dict, s: dict) -> Assessment:
    v = c["valuation"]
    asking, high = v["asking_trillion_krw"], v["fair_value_high_trillion_krw"]
    premium = (asking - high) / high
    evidence = [
        f"요구가격 {_trillion(asking)} vs 추정 적정가치 {v['fair_value_low_trillion_krw']:.1f}~{_trillion(high)}",
        f"적정가치 상단 대비 {_pct(premium)} 프리미엄",
    ]
    ceiling = s.get("price_ceiling_trillion_krw")
    if ceiling is not None and asking > ceiling:
        evidence.append(f"요청 맥락의 내부 기준({_trillion(ceiling)}) 초과 — 가격 타당성 재검토 대상")
    level = "HIGH" if premium > _HIGH_PREMIUM else "MEDIUM" if premium > 0 else None
    if level is None:
        return _a("valuation", "요구가격과 적정가치", "positive", "요구가격이 적정가치 범위 안", evidence)
    return _a(
        "valuation",
        "요구가격과 적정가치",
        "concern",
        f"요구가격이 적정가치 상단을 {_pct(premium)} 상회",
        evidence,
        level=level,
        condition=f"인수 협상 기준가격 {_trillion(high)} 이하",
    )


def _profitability(c: dict, s: dict) -> Assessment:
    f = c["financials"]
    return _a(
        "profitability",
        "수익성",
        "concern",
        f"고성장(CAGR {_pct(f['revenue_cagr_3y'])})이나 영업이익률 {_pct(f['operating_margin'])}로 적자",
        ["인수 후 일정 기간 손익 부담 지속 가능성", "흑자 전환 시점이 가격 정당화의 전제"],
        level="MEDIUM",
    )


def _customer_concentration(c: dict, s: dict) -> Assessment:
    cust = c["customers"]["largest"]
    evidence = [f"최대 고객 {cust['name']} 매출 비중 {_pct(cust['revenue_share'])}"]
    if "customer_concentration" in s.get("confirmed_risks", []):
        evidence.append("요청 맥락에서 최대 고객 관련 외부 리스크가 확인됨 — 이탈 가능성을 실제 리스크로 반영")
    level = "HIGH" if cust["revenue_share"] >= _HIGH_CUSTOMER_SHARE else "MEDIUM"
    return _a(
        "customer_concentration",
        "고객 집중도",
        "concern",
        f"매출의 {_pct(cust['revenue_share'])}가 단일 고객({cust['name']})에 의존",
        evidence,
        level=level,
        condition=f"{cust['name']} 계약 지속 여부 검증",
    )


def _financial_downside(c: dict, s: dict) -> Assessment:
    cust, ip = c["customers"]["largest"], c["technology"]["ip_dispute"]
    return _a(
        "financial_downside",
        "재무적 downside",
        "concern",
        "최대 고객 이탈·특허소송·적자 지속이 겹칠 경우 가치 훼손 폭이 큼",
        [
            f"{cust['name']} 이탈 시 매출 {_pct(cust['revenue_share'])} 노출",
            f"{ip['counterparty']} 특허소송 잠재손실 미산정 ({ip['status']})",
            f"영업이익률 {_pct(c['financials']['operating_margin'])}로 추가 자금 소요 가능",
        ],
        level="MEDIUM",
        condition=f"{ip['counterparty']} 특허소송 잠재손실 검증",
    )


def _deal_terms(c: dict, s: dict) -> Assessment:
    high = c["valuation"]["fair_value_high_trillion_krw"]
    return _a(
        "deal_terms",
        "거래 조건",
        "neutral",
        "가격 상한과 핵심인력 유지를 거래 조건에 연계할 필요",
        [f"기준가격 {_trillion(high)} 이하를 협상 출발점으로 설정", "핵심 설계팀 retention을 earn-out·락업 등 조건으로 구조화"],
        condition="창업자 CTO 및 핵심 설계팀 retention 조건 확보",
    )


def _technology_fit(c: dict, s: dict) -> Assessment:
    t = c["technology"]
    return _a("technology_fit", "기술 적합성", "positive", f"{t['domain']} — 자체 로드맵의 공백 영역", [c["strategic"]["fit"]])


def _key_talent_retention(c: dict, s: dict) -> Assessment:
    t = c["technology"]
    evidence = [f"기술 역량이 {t['core_team']}에 집중"]
    if "key_personnel" in s.get("confirmed_risks", []):
        evidence.append("요청 맥락에서 핵심인력 이탈 관련 리스크가 확인됨 — 인수 후 유지가 실제 변수")
    return _a(
        "key_talent_retention",
        "핵심 기술인력 유지",
        "concern",
        "핵심 설계팀이 이탈하면 인수로 얻는 기술 가치가 사라짐",
        evidence,
        level="HIGH",
        condition="창업자 CTO 및 핵심 설계팀 retention 조건 확보",
    )


def _ip_risk(c: dict, s: dict) -> Assessment:
    ip = c["technology"]["ip_dispute"]
    evidence = [f"{ip['counterparty']}가 제기한 {ip['subject']} 분쟁 ({ip['status']})"]
    if "legal_dispute" in s.get("confirmed_risks", []):
        evidence.append("요청 맥락에서 소송 리스크가 확인됨")
    return _a(
        "ip_risk",
        "IP / 특허 리스크",
        "concern",
        f"핵심 설계({ip['subject']})에 대한 분쟁으로 기술 가치와 손실 규모가 미확정",
        evidence,
        level="MEDIUM",
        condition=f"{ip['counterparty']} 특허소송 잠재손실 검증",
    )


def _integration(c: dict, s: dict) -> Assessment:
    t = c["technology"]
    return _a(
        "integration",
        "기술 통합 가능성",
        "neutral",
        f"통합 기간 {_months(t['post_acquisition_integration_months'])} 예상, 설계 조직의 독립성 유지가 전제",
        [f"인수 후 통합 {_months(t['post_acquisition_integration_months'])}", "핵심 설계팀 유지 여부가 통합 일정의 선행 조건"],
    )


def _risk_opportunity_balance(c: dict, s: dict, others: list[Assessment]) -> Assessment:
    high = [a["label"] for a in others if a["verdict"] == "concern" and a["level"] == "HIGH"]
    positives = [a["label"] for a in others if a["verdict"] == "positive"]
    return _a(
        "risk_opportunity_balance",
        "리스크 / 기회 균형",
        "concern" if high else "positive",
        f"기회 요인 {len(positives)}건 대비 수준 높음 리스크 {len(high)}건 — 조건 충족 시 추진 가치 있음" if high else "두드러진 리스크 없이 기회 요인이 우세",
        [f"기회: {', '.join(positives)}", f"수준 높음 리스크: {', '.join(high) or '없음'}"],
        level="MEDIUM" if high else None,
    )


# Canonical order (also the order of "other considerations" outside the persona's list).
_EVALUATORS: dict[str, Callable[[dict, dict], Assessment]] = {
    "strategic_fit": _strategic_fit,
    "time_to_market": _time_to_market,
    "business_value": _business_value,
    "valuation": _valuation,
    "profitability": _profitability,
    "customer_concentration": _customer_concentration,
    "financial_downside": _financial_downside,
    "deal_terms": _deal_terms,
    "technology_fit": _technology_fit,
    "key_talent_retention": _key_talent_retention,
    "ip_risk": _ip_risk,
    "integration": _integration,
}


def evaluate_all(company: dict[str, Any], signals: dict[str, Any]) -> dict[str, Assessment]:
    out = {key: fn(company, signals) for key, fn in _EVALUATORS.items()}
    out["risk_opportunity_balance"] = _risk_opportunity_balance(company, signals, list(out.values()))
    return out


def recommend(assessments: dict[str, Assessment], company: dict[str, Any]) -> str:
    v = company["valuation"]
    premium = (v["asking_trillion_krw"] - v["fair_value_high_trillion_krw"]) / v["fair_value_high_trillion_krw"]
    if premium > _BLOCKING_PREMIUM:
        return "DO_NOT_PROCEED"
    if any(a["verdict"] == "concern" and a["level"] == "HIGH" for a in assessments.values()):
        return "CONDITIONAL_PROCEED"
    return "PROCEED"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def review_acquisition(
    query: str, company: str, persona: str = "CEO", context: str | None = None, *, path: Path = DATA_PATH
) -> dict[str, Any]:
    """Pure function so the workflow can be unit-tested without an LLM.
    `path` is the company store this system owns (bound by the system's Tool).

    Returns (found case):
        {
          query, company, found: True, persona, persona_label, persona_focus,
          inputs,                 # 입력 정보 정리
          context_signals,        # what was picked up from `context`
          assessments: [...],     # the persona's criteria, in its priority order
          other_considerations,   # remaining criteria, condensed
          positives, concerns,    # from the persona's criteria
          recommendation, recommendation_label,
          conditions              # persona's conditions first, then the rest
        }
    """
    key = resolve_persona(persona)
    if key is None:
        return {"error": f"지원하지 않는 persona: {persona!r}", "supported_personas": list(PERSONAS)}
    c = find_company(company, path)
    if c is None:
        return {"query": query, "company": company, "found": False, "message": f"'{company}'에 대한 기업 데이터가 없습니다."}

    signals = extract_context_signals(context)
    all_assessments = evaluate_all(c, signals)
    persona_keys: list[str] = PERSONAS[key]["criteria"]
    primary = [all_assessments[k] for k in persona_keys]
    others = [all_assessments[k] for k in all_assessments if k not in persona_keys]

    conditions: list[str] = []
    for a in [*primary, *others]:
        if a["condition"] and a["condition"] not in conditions:
            conditions.append(a["condition"])

    recommendation = recommend(all_assessments, c)
    return {
        "query": query,
        "company": c["name"],
        "found": True,
        "persona": key,
        "persona_label": PERSONAS[key]["label"],
        "persona_focus": [a["label"] for a in primary],
        "inputs": summarize_inputs(c),
        "context_signals": signals,
        "assessments": primary,
        "other_considerations": [
            {k: a[k] for k in ("criterion", "label", "verdict", "level", "finding")} for a in others
        ],
        "positives": [a["finding"] for a in primary if a["verdict"] == "positive"],
        "concerns": [
            {"label": a["label"], "level": a["level"], "finding": a["finding"]}
            for a in primary
            if a["verdict"] == "concern"
        ],
        "recommendation": recommendation,
        "recommendation_label": RECOMMENDATION_LABELS[recommendation],
        "conditions": conditions,
    }
