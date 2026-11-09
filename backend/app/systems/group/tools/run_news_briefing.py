"""run_news_briefing — this system's instance: demo fixture data/news.json, internal context from its
data folder and meeting store."""

from __future__ import annotations

from ....library.tools.run_news_briefing import make_run_news_briefing
from .. import DATA_DIR, MEETING_STORE

# A query naming this organization is about all of it: its meetings are found by these topics.
ORG = (("K그룹", "K Group", "그룹"), ("그룹", "계열사"))

run_news_briefing = make_run_news_briefing(DATA_DIR / "news.json", MEETING_STORE, ORG)
