"""run_mna_review — Tool Capability factory: the numbers of an acquisition (facts, DCF, price, customers, technology,
scenarios). It does not recommend: the Advisory Agent judges. The work is in library/workflows/advisory/analysis.py;
`path` is the company data of the system that binds it. Every figure goes into the run's evidence ledger with its id.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from ...runtime import evidence
from ..workflows.advisory.analysis import analyze, for_model
from . import evidence_items
from .meeting_access import plain

TOOL_NAME = "run_mna_review"


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misnamed driver must come back as an error, not vanish

    name: str | None = Field(default=None, validation_alias=AliasChoices("name", "description", "title"),
                             description="시나리오 이름 (예: '알파모바일 매출 절반 상실')")

    @model_validator(mode="before")
    @classmethod
    def _flat(cls, data: Any) -> Any:
        """Drivers a model nests one level down ({"drivers": {...}}) are read as the scenario's own fields."""
        if not isinstance(data, dict):
            return data
        out = {k: v for k, v in data.items() if not isinstance(v, dict)}
        for v in data.values():
            if isinstance(v, dict):
                out.update({k: x for k, x in v.items() if k not in out})
        return out
    largest_customer_revenue_loss: float | None = Field(default=None, description="최대 고객 매출 중 잃는 비율 0~1 (예: 절반 0.5, 전부 1.0)")
    from_year: int | None = Field(default=None, description="변화가 시작되는 연도 (비우면 다음 해부터)")
    revenue_change: float | None = Field(default=None, description="그 밖의 매출 전망 변화율 (예: -10%면 -0.1)")
    operating_margin_change: float | None = Field(default=None, description="영업이익률 변화 (예: -2%p면 -0.02)")
    wacc: float | None = Field(default=None, description="할인율 (예: 12%면 0.12)")
    terminal_growth: float | None = Field(default=None, description="영구성장률 (예: 2%면 0.02)")
    ip_loss: float | None = Field(default=None, description="주주가치에서 뺄 특허 분쟁 손실 가정, 억 원 (예: 1000)")
    price_trillion_krw: float | None = Field(default=None, description="이 시나리오에서만 다르게 볼 인수가격, 조 원")


class Criteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _common_names(cls, data: Any) -> Any:
        """Other names a model uses for the same two criteria (years are converted to months)."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for alias in ("max_price_trillion_krw", "price_cap_trillion_krw", "price_limit_trillion_krw"):
            if alias in data:
                data.setdefault("price_ceiling_trillion_krw", data.pop(alias))
        for alias in ("min_ttm_reduction_years", "min_time_to_market_gain_years", "min_ttm_gain_years"):
            if alias in data:
                years = data.pop(alias)
                data.setdefault("min_time_to_market_gain_months", round(float(years) * 12) if years is not None else None)
        return data

    price_ceiling_trillion_krw: float | None = Field(default=None, description="회의에서 정한 가격 상한 · 재검토 기준, 조 원 (예: '2.5조 원 초과 시 재검토' → 2.5)")
    min_time_to_market_gain_months: int | None = Field(default=None, description="회의에서 정한 최소 Time-to-Market 단축, 개월 (예: '2년 이상' → 24)")


def make_run_mna_review(path: Path) -> BaseTool:
    @tool(TOOL_NAME)
    def run_mna_review(
        query: str,
        company: str,
        price_trillion_krw: float | None = None,
        scenarios: list[Scenario] | None = None,
        criteria: Criteria | None = None,
    ) -> dict[str, Any]:
        """인수 대상 기업의 데이터로 인수 판단에 필요한 사실과 수치를 계산한다. 권고는 돌려주지 않는다 (판단은 경영자문 에이전트가 한다).
        결과: 재무 실적 · 사업계획, 현금흐름 전망으로 계산한 DCF 기업가치 · 주주가치와 민감도, 평가 가격의 DCF 대비 프리미엄,
        운전자본 · 순차입금, 고객 · 계약 · 확정 발주, 기술 · 특허 분쟁 · 핵심인력 · 통합비용, 데이터가 밝힌 미확인 사항(open_items),
        회의 기준 대조(checks), 관점별로 먼저 볼 수치(persona_lenses). 모든 수치에 id가 있어 판단의 근거로 인용한다.
        기업 데이터는 합성(가상) 데이터다.

        Args:
            query: 검토 목적 (예: "뉴로엣지 인수 타당성 — 알파모바일 공급계약 중단 반영").
            company: 인수 대상 기업명 (예: "뉴로엣지").
            price_trillion_krw: 평가할 인수가격, 조 원. 비우면 최초 인수 요구가. 제안가나 사용자가 말한 가격이면 그 값.
            scenarios: 기준과 비교할 변화 (같은 모델로 다시 계산). 새 사건(고객 계약 중단, 소송 등)이나 사용자의 What-if 조건을
                재무 동인으로 옮겨 넣는다. 불확실하면 여러 시나리오(예: 매출 절반 상실 · 전부 상실)를 함께 준다. 최대 4개.
            criteria: 회의에서 정한 기준 (가격 상한, 최소 Time-to-Market 단축). 회의 기록에서 확인한 것만 넣는다.
        """
        ledger = evidence.current()
        aid = f"A{len(ledger.analyses) + 1}" if ledger is not None else "A1"
        result = analyze(company, path=path, query=query, price_trillion_krw=price_trillion_krw,
                         scenarios=[{**{k: v for k, v in plain(s).items() if v is not None}, "name": plain(s).get("name") or f"시나리오 {n}"}
                                    for n, s in enumerate(scenarios or [], start=1)],
                         criteria={k: v for k, v in plain(criteria).items() if v is not None} if criteria else None, analysis_id=aid)
        if ledger is not None and result.get("found") and not result.get("error"):
            ledger.analyses.append(result)
        evidence_items.recorded(result, evidence_items.analysis)
        return for_model(result)  # the full result (yearly tables, …) stays in the ledger for the record and the report

    return run_mna_review
