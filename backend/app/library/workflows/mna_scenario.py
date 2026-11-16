"""M&A What-if 시나리오: 기업 데이터 변경 함수와 변경 전후 비교 workflow.

`review_what_if(..., path=...)`가 run_mna_review Tool의 What-if 경로 진입점이다.
"""

from copy import deepcopy
from math import isfinite
from pathlib import Path
from typing import Any

from .mna_review import (
    PERSONAS,
    RECOMMENDATION_LABELS,
    evaluate_all,
    extract_context_signals,
    find_company,
    summarize_inputs,
)


def apply_price_scenario(
    company: dict[str, Any],
    asking_trillion_krw: float,
) -> dict[str, Any]:
    """원본을 보존하면서 가정한 인수가격을 적용한다."""

    if (
        isinstance(asking_trillion_krw, bool)
        or not isinstance(asking_trillion_krw, (int, float))
        or not isfinite(asking_trillion_krw)
        or asking_trillion_krw <= 0
    ):
        raise ValueError("인수가격은 0보다 큰 유한한 숫자여야 합니다.")

    scenario_company = deepcopy(company)

    scenario_company["valuation"]["asking_trillion_krw"] = float(
        asking_trillion_krw
    )

    return scenario_company


def compare_price_scenarios(
    company: dict[str, Any],
    new_asking_price: float,
) -> dict[str, Any]:
    """인수가격 변경 전후의 평가 결과를 비교한다."""

    from .mna_review import _valuation

    scenario_company = apply_price_scenario(
        company,
        new_asking_price,
    )

    before = _valuation(company, {})
    after = _valuation(scenario_company, {})

    return {
        "company": company["name"],
        "before": {
            "asking_price": company["valuation"]["asking_trillion_krw"],
            "verdict": before["verdict"],
            "risk_level": before["level"],
            "finding": before["finding"],
        },
        "after": {
            "asking_price": scenario_company["valuation"]["asking_trillion_krw"],
            "verdict": after["verdict"],
            "risk_level": after["level"],
            "finding": after["finding"],
        },
        "changed": (
            before["verdict"] != after["verdict"]
            or before["level"] != after["level"]
        ),
    }


def apply_profitability_scenario(
    company: dict[str, Any],
    operating_margin: float,
) -> dict[str, Any]:
    """원본을 유지하면서 가정한 영업이익률을 적용한다."""

    if (
        isinstance(operating_margin, bool)
        or not isinstance(operating_margin, (int, float))
        or not isfinite(operating_margin)
        or not -1 <= operating_margin <= 1
    ):
        raise ValueError("영업이익률은 -1부터 1 사이의 유한한 숫자여야 합니다.")

    scenario_company = deepcopy(company)
    scenario_company["financials"]["operating_margin"] = float(
        operating_margin
    )

    return scenario_company


def apply_customer_scenario(
    company: dict[str, Any],
    revenue_share: float,
) -> dict[str, Any]:
    """원본을 유지하면서 최대 고객 매출 비중을 변경한다."""

    if (
        isinstance(revenue_share, bool)
        or not isinstance(revenue_share, (int, float))
        or not isfinite(revenue_share)
        or not 0 <= revenue_share <= 1
    ):
        raise ValueError("고객 매출 비중은 0부터 1 사이여야 합니다.")

    scenario_company = deepcopy(company)
    scenario_company["customers"]["largest"]["revenue_share"] = float(
        revenue_share
    )

    return scenario_company


def apply_scenarios(
    company: dict[str, Any],
    *,
    asking_trillion_krw: float | None = None,
    operating_margin: float | None = None,
    revenue_share: float | None = None,
) -> dict[str, Any]:
    """주어진 변경 조건을 모두 적용한 기업 데이터 복사본. 원본은 바꾸지 않는다."""

    scenario_company = deepcopy(company)

    if asking_trillion_krw is not None:
        scenario_company = apply_price_scenario(
            scenario_company, asking_trillion_krw
        )

    if operating_margin is not None:
        scenario_company = apply_profitability_scenario(
            scenario_company, operating_margin
        )

    if revenue_share is not None:
        scenario_company = apply_customer_scenario(
            scenario_company, revenue_share
        )

    return scenario_company


def compare_mna_scenarios(
    company: dict[str, Any],
    *,
    asking_trillion_krw: float | None = None,
    operating_margin: float | None = None,
    revenue_share: float | None = None,
    context: str | None = None,
) -> dict[str, Any]:
    """M&A 조건 변경 전후의 전체 평가 결과를 비교한다."""

    from .mna_review import (
        evaluate_all,
        recommend,
        extract_context_signals,
    )

    if all(
        value is None
        for value in (
            asking_trillion_krw,
            operating_margin,
            revenue_share,
        )
    ):
        raise ValueError("최소 하나 이상의 변경 조건이 필요합니다.")

    # 원본 기업 데이터를 보존한다.
    scenario_company = apply_scenarios(
        company,
        asking_trillion_krw=asking_trillion_krw,
        operating_margin=operating_margin,
        revenue_share=revenue_share,
    )

    # 기존 회의 맥락을 변경 전후 평가에 동일하게 반영한다.
    context_signals = extract_context_signals(context)

    # 기존 M&A 평가 함수와 판단 기준을 그대로 사용한다.
    before = evaluate_all(company, context_signals)
    after = evaluate_all(scenario_company, context_signals)


    # 항목별 결과를 비교한다.
    changes = []

    for criterion in before:
        old = before[criterion]
        new = after[criterion]

        if (
            old["verdict"] != new["verdict"]
            or old["level"] != new["level"]
            or old["finding"] != new["finding"]
        ):
            changes.append(
                {
                    "criterion": criterion,
                    "label": old["label"],
                    "before": {
                        "verdict": old["verdict"],
                        "level": old["level"],
                        "finding": old["finding"],
                    },
                    "after": {
                        "verdict": new["verdict"],
                        "level": new["level"],
                        "finding": new["finding"],
                    },
                }
            )

    # 변경 전후 권고 조건 수집 (새로 추가)
    def collect_conditions(assessments):
        conditions = []

        for assessment in assessments.values():
            condition = assessment.get("condition")

            if condition and condition not in conditions:
                conditions.append(condition)

        return conditions

    before_conditions = collect_conditions(before)
    after_conditions = collect_conditions(after)

    return {
        "company": company["name"],
        "assumptions": {
            "asking_trillion_krw": asking_trillion_krw,
            "operating_margin": operating_margin,
            "revenue_share": revenue_share,
        },
        "before_recommendation": recommend(before, company),
        "after_recommendation": recommend(after, scenario_company),

        # 변경 전후 권고 조건 (새로 추가)
        "before_conditions": before_conditions,
        "after_conditions": after_conditions,
        "removed_conditions": [
            condition
            for condition in before_conditions
            if condition not in after_conditions
        ],
        "added_conditions": [
            condition
            for condition in after_conditions
            if condition not in before_conditions
        ],

        "changes": changes,
    }


def compare_persona_scenarios(
    company: dict[str, Any],
    persona: str = "CEO",
    *,
    asking_trillion_krw: float | None = None,
    operating_margin: float | None = None,
    revenue_share: float | None = None,
    context: str | None = None,
) -> dict[str, Any]:
    """경영진 관점에 따라 What-if 평가 결과를 정리한다."""

    from .mna_review import PERSONAS, resolve_persona

    persona_key = resolve_persona(persona)

    if persona_key is None:
        raise ValueError(f"지원하지 않는 persona: {persona}")

    result = compare_mna_scenarios(
        company,
        asking_trillion_krw=asking_trillion_krw,
        operating_margin=operating_margin,
        revenue_share=revenue_share,
        context=context,
    )
    priority = PERSONAS[persona_key]["criteria"]

    # 해당 경영진이 중요하게 보는 평가 항목을 먼저 표시
    changes = sorted(
        result["changes"],
        key=lambda item: (
            0 if item["criterion"] in priority else 1,
            priority.index(item["criterion"])
            if item["criterion"] in priority
            else 0,
        ),
    )

    result["persona"] = persona_key
    result["persona_label"] = PERSONAS[persona_key]["label"]
    result["persona_focus"] = priority
    result["changes"] = changes

    return result


def review_what_if(
    query: str,
    company: str,
    persona: str = "CEO",
    context: str | None = None,
    *,
    path: Path,
    asking_trillion_krw: float | None = None,
    operating_margin: float | None = None,
    revenue_share: float | None = None,
) -> dict[str, Any]:
    """What-if 진입점 (run_mna_review의 변경 조건 경로). `path`는 system이 바인딩한 기업 store.

    변경 전후 비교(compare_persona_scenarios)에 기존 평가 결과와 같은 이름의 필드
    (inputs · assessments · recommendation · conditions …)를 더해 돌려준다. 모르는 기업 ·
    지원하지 않는 persona · 잘못된 변경 값은 예외 대신 found: false / error로 돌려준다.
    """

    target = find_company(company, path)

    if target is None:
        return {
            "query": query,
            "company": company,
            "found": False,
            "message": f"'{company}'에 대한 기업 데이터가 없습니다.",
        }

    if persona.upper() not in ("CEO", "CFO", "CTO"):
        return {
            "error": f"지원하지 않는 persona: {persona!r}",
            "supported_personas": ["CEO", "CFO", "CTO"],
        }

    changes = {
        "asking_trillion_krw": asking_trillion_krw,
        "operating_margin": operating_margin,
        "revenue_share": revenue_share,
    }

    try:
        result = compare_persona_scenarios(
            target, persona=persona, context=context, **changes
        )
    except ValueError as exc:
        return {
            "query": query,
            "company": target["name"],
            "found": True,
            "error": str(exc),
            "error_type": "INVALID_SCENARIO",
        }

    scenario_company = apply_scenarios(target, **changes)

    # 기존 평가 결과와 같은 이름의 필드 (UI · Agent가 같은 방식으로 읽는다)
    assessments = evaluate_all(
        scenario_company,
        extract_context_signals(context),
    )
    primary = [assessments[key] for key in PERSONAS[result["persona"]]["criteria"]]

    return {
        **result,

        # What-if 변경 전후 원본 데이터
        "before_inputs": summarize_inputs(target),
        "after_inputs": summarize_inputs(scenario_company),

        "query": query,
        "found": True,
        "persona_focus": [item["label"] for item in primary],
        "inputs": summarize_inputs(scenario_company),
        "assessments": primary,
        "recommendation": result["after_recommendation"],
        "recommendation_label": RECOMMENDATION_LABELS[
            result["after_recommendation"]
        ],
        "conditions": result["after_conditions"],

        # What-if 결과 구분
        "mode": "what_if",
        "notice": "변경 조건에 따른 규칙 기반 M&A 분석 결과입니다.",
    }
