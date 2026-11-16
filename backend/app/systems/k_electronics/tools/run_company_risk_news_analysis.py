"""run_company_risk_news_analysis — K Electronics' instance, bound to this system's news store (seeded from data/news.json)."""

from __future__ import annotations

from ....library.tools.run_company_risk_news_analysis import make_run_company_risk_news_analysis
from .. import NEWS_STORE

run_company_risk_news_analysis = make_run_company_risk_news_analysis(NEWS_STORE)
