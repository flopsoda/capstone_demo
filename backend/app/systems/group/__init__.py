"""Group agent system (the holding group's platform).

    app.py       FastAPI app: `uvicorn app.systems.group.app:app --port 8001`
    registry.py  this system's CAPABILITIES with permissions
    agents/      group_super (super core) · meeting / news / advisory (specialist core, Group data)
    skills/      Skills specific to this system (library Skills are registered from app/library)
    tools/       delegate_to_* (handoffs to this system's specialists) · request_k_electronics_review (A2A client to K Electronics)
                 · run_* bound to data/
    data/        this system's fixtures (virtual); meetings.json seeds its meeting store (schema `grp`); live news in schema `grp_news`

Talks to K Electronics only through its A2A surface (HTTP); never imports it.
"""

from __future__ import annotations

from pathlib import Path

from ...library.workflows.meeting_store import MeetingStore
from ...library.workflows.news.store import NewsStore

SYSTEM = "group"  # stamped on every event this system emits
DATA_DIR = Path(__file__).resolve().parent / "data"
# This system's meeting records: its own schema in the meetings database, seeded from data/meetings.json.
MEETING_STORE = MeetingStore("grp", seed=DATA_DIR / "meetings.json")
# This system's live news (articles, events, analyses): its own schema in the news database.
NEWS_STORE = NewsStore("grp_news")
