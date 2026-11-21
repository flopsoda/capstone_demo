"""K Electronics agent system.

    app.py       FastAPI app (uvicorn app.main:app → this)
    registry.py  this system's CAPABILITIES with permissions
    agents/      k_super (super core) · meeting / news / advisory (specialist core)
    tools/       delegate_to_* (handoffs to this system's specialists) · run_* bound to data/
    data/        this system's fixtures (virtual); meetings.json seeds its meeting store (schema `kelec`); news.json seeds its news store (schema `kelec_news`)
"""

from __future__ import annotations

from pathlib import Path

from ...library.workflows.kg.store import KGStore
from ...library.workflows.meeting_store import MeetingStore
from ...library.workflows.news.store import NewsStore

SYSTEM = "k_electronics"  # stamped on every event this system emits
DATA_DIR = Path(__file__).resolve().parent / "data"
# This system's meeting records: its own schema in the meetings database, seeded from data/meetings.json.
MEETING_STORE = MeetingStore("kelec", seed=DATA_DIR / "meetings.json")
# This system's news (articles, events, analyses): its own schema in the news database, seeded from
# data/news.json; the publisher feeds add to it on every request and scheduled collection.
NEWS_STORE = NewsStore("kelec_news", seed=DATA_DIR / "news.json")
# This system's Enterprise KG: built from its meeting records and its news (never another system's), in its own schema;
# the companies of data/companies.json are its seed entities.
KG_STORE = KGStore(MEETING_STORE, NEWS_STORE, aliases=DATA_DIR / "companies.json")
