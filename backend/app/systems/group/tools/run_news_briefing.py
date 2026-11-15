"""run_news_briefing — the Group system's instance, bound to this system's demo fixture data/news.json and its news store."""

from __future__ import annotations

from ....library.tools.run_news_briefing import make_run_news_briefing
from .. import DATA_DIR, NEWS_STORE

run_news_briefing = make_run_news_briefing(DATA_DIR / "news.json", NEWS_STORE)
