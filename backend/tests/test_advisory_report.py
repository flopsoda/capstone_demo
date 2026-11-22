"""투자심의 보고서 PDF (library/workflows/advisory/report): drawn from a stored judgement only, with less data → fewer sections."""

import re
from pathlib import Path

import pytest

from app.library.workflows.advisory import report
from app.library.workflows.advisory.analysis import analyze

needs_fonts = pytest.mark.skipif(not report.fonts_available(), reason="PDF 글꼴(나눔고딕) 없음 — agent-backend 이미지에서 실행")

SYSTEMS = Path(__file__).resolve().parent.parent / "app" / "systems"
K_DATA = SYSTEMS / "k_electronics" / "data" / "companies.json"
GROUP_DATA = SYSTEMS / "group" / "data" / "companies.json"
CRITERIA = {"price_ceiling_trillion_krw": 2.5, "min_time_to_market_gain_months": 24}

OPINION = {
    "headline": "전략적 가치는 크나 가격 조정을 전제로 조건부 진행",
    "summary": "최초 요구가는 DCF 주주가치보다 높다.",
    "rationale": [{"statement": "가격이 회의 기준을 넘는다", "evidence": ["A1.check.price_ceiling", "MTG-2026-09-15-D1"]}],
    "personas": [{"persona": p, "stance": "CONDITIONAL_PROCEED", "judgement": f"{p} 판단", "key_points": [{"statement": "근거", "evidence": ["A1.dcf.equity_value"]}]}
                 for p in ("CEO", "CFO", "CTO")],
    "risks": [{"title": "가격 프리미엄", "level": "HIGH", "assessment": "요구가가 높다", "evidence": ["A1.price.premium_vs_dcf"], "response": "가격 협상"}],
    "conditions": [{"condition": "가격 조정", "addresses": "가격 프리미엄", "evidence": ["A2.price.premium_vs_dcf"]}],
    "uncertainties": ["변경지배권 동의 미확인"],
    "sections": {"financial": "재무 의견", "valuation": "가치 의견", "commercial": "고객 의견", "technology": "기술 의견"},
}
DECISION = {"id": "MTG-2026-09-15-D1", "kind": "meeting_decision", "text": "2.5조 원 초과 시 재검토", "speaker": "CFO",
            "meeting": "MTG-2026-09-15", "meeting_title": "AI 반도체 전략회의", "date": "2026-09-15"}
ARTICLE = {"id": "NEWS-2026-11-17-01", "kind": "article", "title": "최대 고객 공급계약 중단", "date": "2026-11-17", "outlet": "반도체경제 (가상)",
           "text": "공급계약 중단 발표"}


def stored(analyses, opinion, *, code, records=(), rec="CONDITIONAL_PROCEED", price=2.8, evaluation="initial", prior_code=None):
    items = {i["id"]: i for a in analyses for i in a["items"]}
    items.update({r["id"]: r for r in records})
    return {"code": code, "company": analyses[0]["company"], "evaluation_type": evaluation, "recommendation": rec,
            "price_trillion_krw": price, "opinion": {**opinion, "recommendation": rec, "evaluation_type": evaluation},
            "evidence": {"items": items, "analyses": analyses}, "prior_code": prior_code, "classification": 3,
            "meeting_codes": ["MTG-2026-09-15"], "evidence_as_of": "2026-11-17", "created_by": "ceo@example.com",
            "created_at": "2026-11-22T15:40+09:00"}


def neuroedge(scenarios=()):
    return [analyze("뉴로엣지", path=K_DATA, criteria=CRITERIA, analysis_id="A1", scenarios=list(scenarios)),
            analyze("뉴로엣지", path=K_DATA, criteria=CRITERIA, price_trillion_krw=2.2, analysis_id="A2")]


def pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?![s\w])", pdf))


def test_filename_and_disposition():
    review = {"code": "ADV-2026-11-22-01", "company": "뉴로/엣지"}
    assert report.filename(review) == "뉴로 엣지_투자심의보고서_ADV-2026-11-22-01.pdf"
    header = report.content_disposition(report.filename({"code": "ADV-1", "company": "뉴로엣지"}))
    assert header.startswith('attachment; filename="advisory_report.pdf"; filename*=UTF-8\'\'') and "%EB%89%B4" in header


def test_main_analysis_is_the_evaluated_price():
    r = report._Review(stored(neuroedge(), OPINION, code="ADV-1", price=2.2))
    assert r.main["analysis_id"] == "A2" and [a["analysis_id"] for a in r.others] == ["A1"]
    assert report.price_basis(r.main) == "제안가 2.2조 원"


@needs_fonts
def test_initial_review_renders():
    pdf = report.render(stored(neuroedge(), OPINION, code="ADV-2026-11-12-01", records=[DECISION]))
    assert pdf.startswith(b"%PDF") and pages(pdf) >= 6


@needs_fonts
def test_reevaluation_with_prior_and_scenarios():
    prior = stored(neuroedge(), OPINION, code="ADV-2026-11-12-01", records=[DECISION])
    scen = [{"name": "최대 고객 매출 절반 상실", "largest_customer_revenue_loss": 0.5}]
    current = stored(neuroedge(scen), {**OPINION, "changes_from_prior": "공급계약 중단으로 권고를 바꾼다.", "prior_id": prior["code"]},
                     code="ADV-2026-11-22-01", records=[DECISION, ARTICLE], rec="DO_NOT_PROCEED", evaluation="reevaluation",
                     prior_code=prior["code"])
    with_prior = report.render(current, prior)
    assert with_prior.startswith(b"%PDF")
    assert report.render(current).startswith(b"%PDF")  # the change section without the prior record at hand
    assert pages(with_prior) > pages(report.render(prior))


@needs_fonts
def test_company_with_little_data_gets_a_shorter_report():
    """The Group system's company has no balance-sheet detail, patents, people or integration data."""
    a = analyze("브라이트셀", path=GROUP_DATA)
    assert a["found"] and not a["tables"]["integration_cost"] and not a["tables"]["ip_disputes"]
    opinion = {"headline": "진행", "summary": "요약", "rationale": [{"statement": "프리미엄", "evidence": ["A1.price.premium_vs_dcf"]}],
               "personas": [{"persona": "CEO", "stance": "PROCEED", "judgement": "필요하다"}]}
    small = report.render(stored([a], opinion, code="ADV-2026-11-20-01", rec="PROCEED", price=1.6))
    full = report.render(stored(neuroedge(), OPINION, code="ADV-2026-11-12-01", records=[DECISION]))
    assert small.startswith(b"%PDF") and pages(small) < pages(full)


@needs_fonts
def test_review_without_evidence_does_not_crash():
    assert report.render({"code": "ADV-1", "company": "가상기업", "recommendation": "DO_NOT_PROCEED", "opinion": {"headline": "보류"}}).startswith(b"%PDF")
