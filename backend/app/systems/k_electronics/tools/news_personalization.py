"""News personalization Tools — this system's instances, bound to its data folder
(data/news_subscribers.json, data/news.json)."""

from __future__ import annotations

from ....library.tools.news_personalization import make_manage_news_interests, make_run_personal_news_briefing
from .. import DATA_DIR, MEETING_STORE
from .run_news_briefing import ORG

manage_news_interests = make_manage_news_interests(DATA_DIR)
run_personal_news_briefing = make_run_personal_news_briefing(DATA_DIR, MEETING_STORE, ORG)
