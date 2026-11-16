"""run_news_briefing — K Electronics' instance, bound to this system's news store (seeded from data/news.json)."""

from __future__ import annotations

from ....library.tools.run_news_briefing import make_run_news_briefing
from .. import NEWS_STORE

run_news_briefing = make_run_news_briefing(NEWS_STORE)
