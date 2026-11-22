"""The shared workflows over the Group system's own stores (same code as the K tests, different data)."""

from app.library.workflows.meeting_context_retrieval import retrieve_meeting_context
from app.library.workflows.meeting_store import Viewer
from app.library.workflows.advisory.analysis import analyze
from app.systems.group import DATA_DIR, MEETING_STORE
from app.systems.group.tools.run_company_risk_news_analysis import run_company_risk_news_analysis as group_risk_news

EXECUTIVE = Viewer("ceo@example.com", "", frozenset({"executive"}), 4, True)
COMPANIES = DATA_DIR / "companies.json"


def test_group_meeting_store_has_the_investment_committee_premeeting(meeting_db):
    out = retrieve_meeting_context("뉴로엣지 인수안 그룹 투자심의 기준", ["투자심의", "뉴로엣지"], store=MEETING_STORE, viewer=EXECUTIVE)
    assert out["matched"] >= 1
    m = out["meetings"][0]  # best match first
    assert m["title"] == "그룹 투자심의위원회 사전회의"
    assert any("계열사 슈퍼에이전트의 검토 결과를 전제" in d["statement"] for d in m["decisions"])
    assert [a["text"] for a in m["open_action_items"]] == ["K전자 뉴로엣지 인수안의 계열사 검토 결과 수령"]


def news(query, company):
    return group_risk_news.invoke({"query": query, "company": company})


def test_group_news_store_covers_subsidiaries_and_group_targets(news_db, monkeypatch):
    from app.library.tools import run_news_briefing as briefing_tool

    monkeypatch.setattr(briefing_tool, "_llm", lambda: None)  # the store's analyses, no model
    out = news("핵심인력 이탈", "K바이오")
    assert out["matched"] == 1 and out["risks"][0]["category"] == "key_personnel" and out["risks"][0]["level"] == "HIGH"
    out = news("리스크", "K그룹")
    assert [r["level"] for r in out["risks"]] == ["HIGH", "MEDIUM", "MEDIUM", "LOW"]  # HIGH first, then newest
    assert news("최대 고객", "브라이트셀")["risks"][0]["category"] == "customer_concentration"


def test_group_company_store_analyzes_the_group_level_target():
    out = analyze("브라이트셀", path=COMPANIES, query="브라이트셀 인수 검토")
    assert out["found"] is True and "recommendation" not in out  # the numbers only; the advisory agent judges
    figures = {i["id"]: i for i in out["items"]}
    assert figures["A1.cust.largest_share"]["display"] == "52.0%" and out["tables"]["customers"][0]["name"] == "오토원"
    assert figures["A1.dcf.equity_value"]["value"] > 0 and figures["A1.price.premium_vs_dcf"]["unit"] == "%"
    assert analyze("뉴로엣지", path=COMPANIES)["found"] is False
