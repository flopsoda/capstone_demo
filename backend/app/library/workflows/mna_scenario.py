
"""M&A What-if 시나리오용 기업 데이터 변경 함수."""

from copy import deepcopy
from math import isfinite
from typing import Any


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



def compare_mna_scenarios(
    company: dict[str, Any],
    *,
    asking_trillion_krw: float | None = None,
    operating_margin: float | None = None,
    revenue_share: float | None = None,
) -> dict[str, Any]:
    """M&A 조건 변경 전후의 전체 평가 결과를 비교한다."""

    from .mna_review import evaluate_all, recommend

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

    # 기존 평가 함수를 재사용한다.
    before = evaluate_all(company, {})
    after = evaluate_all(scenario_company, {})

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
