"""run_company_risk_news_analysis — the Group system's instance, bound to this system's demo fixture data/news.json and its news store."""

from __future__ import annotations

from ....library.tools.run_company_risk_news_analysis import make_run_company_risk_news_analysis
from .. import DATA_DIR, NEWS_STORE

run_company_risk_news_analysis = make_run_company_risk_news_analysis(DATA_DIR / "news.json", NEWS_STORE)
