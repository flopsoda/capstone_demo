"""The shared workflows over the Group system's own stores (same code as the K tests, different data)."""

from app.library.workflows.company_risk_news_analysis import analyze_company_risk_news
from app.library.workflows.meeting_context_retrieval import retrieve_meeting_context
from app.library.workflows.meeting_store import Viewer
from app.library.workflows.mna_review import review_acquisition
from app.systems.group import DATA_DIR, MEETING_STORE

EXECUTIVE = Viewer("ceo@example.com", "", frozenset({"executive"}), 4, True)
NEWS = DATA_DIR / "news.json"
COMPANIES = DATA_DIR / "companies.json"


def test_group_meeting_store_has_the_investment_committee_premeeting(meeting_db):
    out = retrieve_meeting_context("뉴로엣지 인수안 그룹 투자심의 기준", ["투자심의", "뉴로엣지"], store=MEETING_STORE, viewer=EXECUTIVE)
    assert out["matched"] >= 1
    m = out["meetings"][0]  # best match first
    assert m["title"] == "그룹 투자심의위원회 사전회의"
    assert any("계열사 슈퍼에이전트의 검토 결과를 전제" in d["statement"] for d in m["decisions"])
    assert [a["text"] for a in m["open_action_items"]] == ["K전자 뉴로엣지 인수안의 계열사 검토 결과 수령"]


def test_group_news_store_covers_subsidiaries_and_group_targets():
    out = analyze_company_risk_news("핵심인력 이탈", "K바이오", path=NEWS)
    assert out["matched"] == 1 and out["risks"][0]["category"] == "key_personnel" and out["risks"][0]["level"] == "HIGH"
    out = analyze_company_risk_news("리스크", "K그룹", path=NEWS)
    assert [r["level"] for r in out["risks"]] == ["HIGH", "MEDIUM", "MEDIUM", "LOW"]  # HIGH first, then newest
    assert analyze_company_risk_news("최대 고객", "브라이트셀", path=NEWS)["risks"][0]["category"] == "customer_concentration"


def test_group_company_store_reviews_the_group_level_target():
    out = review_acquisition("브라이트셀 인수 검토", "브라이트셀", "CFO", path=COMPANIES)
    assert out["found"] is True and out["persona"] == "CFO"
    assert out["recommendation"] in {"PROCEED", "CONDITIONAL_PROCEED", "DO_NOT_PROCEED"}
    assert out["inputs"]["customers"]["largest"] == "오토원"
    assert review_acquisition("뉴로엣지", "뉴로엣지", "CEO", path=COMPANIES)["found"] is False
