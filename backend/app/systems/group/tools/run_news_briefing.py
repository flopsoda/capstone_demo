"""run_news_briefing — this system's instance; the demo fixture source is its data/news.json."""

from __future__ import annotations

from ....library.tools.run_news_briefing import make_run_news_briefing
from .. import DATA_DIR

run_news_briefing = make_run_news_briefing(DATA_DIR / "news.json")
