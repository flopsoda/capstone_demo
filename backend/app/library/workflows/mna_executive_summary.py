"""CEO, CFO, CTO 통합 M&A 경영진 평가."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .mna_review import review_acquisition

def extract_key_risks(reviews: dict[str, dict]) -> list[dict]:
    """경영진의 우려 항목을 중복 제거하고 위험 등급순으로 정렬한다."""
    risks = {}
    priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

    for persona, review in reviews.items():
        for item in review.get("assessments", []):
            if item.get("verdict") != "concern":
                continue

            criterion = item.get("criterion")
            if not criterion:
                continue

            if criterion not in risks:
                risks[criterion] = {
                    "criterion": criterion,
                    "label": item.get("label", criterion),
                    "level": item.get("level"),
                    "finding": item.get("finding", ""),
                    "personas": [],
                }

            # 동일 위험에 대해 더 높은 등급이 나오면 갱신
            current_level = risks[criterion]["level"]
            new_level = item.get("level")

            if priority.get(new_level, 3) < priority.get(current_level, 3):
                risks[criterion]["level"] = new_level
                risks[criterion]["finding"] = item.get("finding", "")
                risks[criterion]["label"] = item.get("label", criterion)

            if persona not in risks[criterion]["personas"]:
                risks[criterion]["personas"].append(persona)

    return sorted(
        risks.values(),
        key=lambda risk: (
            priority.get(risk["level"], 3),
            risk["criterion"],
        ),
    )


def extract_key_opportunities(reviews: dict[str, dict]) -> list[dict]:
    """경영진의 긍정 평가를 중복 없이 통합한다."""
    opportunities = {}

    for persona, review in reviews.items():
        for item in review.get("assessments", []):
            if item.get("verdict") != "positive":
                continue

            criterion = item.get("criterion")
            if not criterion:
                continue

            if criterion not in opportunities:
                opportunities[criterion] = {
                    "criterion": criterion,
                    "label": item.get("label", criterion),
                    "finding": item.get("finding", ""),
                    "personas": [],
                }

            if persona not in opportunities[criterion]["personas"]:
                opportunities[criterion]["personas"].append(persona)

    return list(opportunities.values())


def build_decision_rationale(
    recommendation: str,
    key_risks: list[dict],
    conditions: list[str],
    key_opportunities: list[dict],
) -> dict:
    """긍정 요인과 핵심 위험을 바탕으로 최종 권고 근거를 정리한다."""

    high_risks = [
        risk["label"]
        for risk in key_risks
        if risk["level"] == "HIGH"
    ]

    positive_factors = [
        item["label"]
        for item in key_opportunities
    ]

    positive_text = (
        ", ".join(positive_factors)
        if positive_factors
        else "확인된 긍정 요인 없음"
    )

    risk_text = (
        ", ".join(high_risks)
        if high_risks
        else "HIGH 등급 위험 없음"
    )

    condition_text = (
        "; ".join(conditions)
        if conditions
        else "추가 인수 조건 없음"
    )

    return {
        "recommendation": recommendation,
        "positive_factors": positive_factors,
        "high_risks": high_risks,
        "required_conditions": conditions,
        "summary": (
            f"최종 권고는 {recommendation}입니다. "
            f"주요 긍정 요인은 {positive_text}입니다. "
            f"우선 검토할 HIGH 등급 위험은 {risk_text}입니다. "
            f"인수 진행 전 검토할 조건은 {len(conditions)}건입니다. "
            f"세부 조건: {condition_text}"
        ),
    }


def review_executive_summary(
    query: str,
    company: str,
    context: str | None = None,
    *,
    path: Path,
) -> dict[str, Any]:
    """기존 개별 평가 결과를 통합한다."""

    reviews = {
        persona: review_acquisition(
            query=query,
            company=company,
            persona=persona,
            context=context,
            path=path,
        )
        for persona in ("CEO", "CFO", "CTO")
    }

    first = reviews["CEO"]

    if not first.get("found"):
        return first

    key_risks = extract_key_risks(reviews)
    key_opportunities = extract_key_opportunities(reviews)

    return {
        "query": query,
        "company": first["company"],
        "found": True,
        "persona": "ALL",
        "mode": "executive_summary",
        "inputs": first["inputs"],
        "context_signals": first["context_signals"],
        "key_risks": key_risks,
        "key_opportunities": key_opportunities,
        "decision_rationale": build_decision_rationale(
            first["recommendation"],
            key_risks,
            first["conditions"],
            key_opportunities,
        ),
        "executive_reviews": {
            persona: {
                "persona_focus": result["persona_focus"],
                "assessments": result["assessments"],
                "positives": result["positives"],
                "concerns": result["concerns"],
            }
            for persona, result in reviews.items()
        },
        "recommendation": first["recommendation"],
        "recommendation_label": first["recommendation_label"],
        "conditions": first["conditions"],

        
        "verified_report": "\n".join([
            "## M&A 경영진 종합 보고서",
            "",
            "### 1. 기업 개요",
            f"- 인수 대상: {first['company']}",
            f"- 주요 사업: {first['inputs']['business']}",
            "",
            "### 2. 재무 및 기업가치",
            f"- 연매출: {first['inputs']['financials']['revenue']}",
            f"- 3년 매출 CAGR: {first['inputs']['financials']['revenue_cagr_3y']}",
            f"- 영업이익률: {first['inputs']['financials']['operating_margin']}",
            f"- 인수 요구가격: {first['inputs']['valuation']['asking']}",
            f"- 적정가치: {first['inputs']['valuation']['fair_value']}",
            "",
            "### 3. 주요 고객 및 기술",
            f"- 최대 고객: {first['inputs']['customers']['largest']}",
            f"- 매출 비중: {first['inputs']['customers']['largest_share']}",
            f"- 특허 분쟁: {first['inputs']['technology']['ip_dispute']}",
            "",
            "### 4. CEO·CFO·CTO 관점별 평가",
            *[
                line
                for persona in ("CEO", "CFO", "CTO")
                for line in [
                    f"- {persona} 관점",
                    *[
                        f"  - {item['label']}: {item['finding']}"
                        for item in reviews[persona]["assessments"]
                    ],
                ]
            ],
            "",
            "### 5. 핵심 위험",
            *[
                f"- [{risk['level']}] {risk['label']}: {risk['finding']}"
                for risk in key_risks
            ],
            "",
            "### 6. 인수 기회",
            *[
                f"- {item['label']}: {item['finding']}"
                for item in key_opportunities
            ],
            "",
            "### 7. 인수 조건",
            *[
                f"- {condition}"
                for condition in first["conditions"]
            ],
            "",
            "### 8. 최종 권고",
            f"**{first['recommendation_label']}** "
            f"({first['recommendation']})",
        ]),
    }

