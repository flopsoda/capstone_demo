"""Group agent system (the holding group's platform).

    app.py       FastAPI app: `uvicorn app.systems.group.app:app --port 8001`
    registry.py  this system's CAPABILITIES with permissions
    agents/      group_super (super core) · meeting / news / advisory (specialist core, Group data)
    skills/      Skills specific to this system (library Skills are registered from app/library)
    tools/       delegate_to_* (handoffs to this system's specialists) · request_k_electronics_review (A2A client to K Electronics)
                 · run_* bound to data/
    data/        this system's fixtures (virtual); meetings.json seeds its meeting store (schema `grp`); news.json seeds its news store (schema `grp_news`)

Talks to K Electronics only through its A2A surface (HTTP); never imports it.
"""

from __future__ import annotations

from pathlib import Path

from ...library.workflows.kg.store import KGStore
from ...library.workflows.meeting_store import MeetingStore
from ...library.workflows.memory.memory import SuperAgentMemory
from ...library.workflows.memory.store import MemoryStore
from ...library.workflows.news.store import NewsStore

SYSTEM = "group"  # stamped on every event this system emits
DATA_DIR = Path(__file__).resolve().parent / "data"
# This system's meeting records: its own schema in the meetings database, seeded from data/meetings.json.
MEETING_STORE = MeetingStore("grp", seed=DATA_DIR / "meetings.json")
# This system's news (articles, events, analyses): its own schema in the news database, seeded from
# data/news.json; the publisher feeds add to it on every request and scheduled collection.
NEWS_STORE = NewsStore("grp_news", seed=DATA_DIR / "news.json")
# This system's Enterprise KG: built from its meeting records and its news (never another system's), in its own schema;
# the companies of data/companies.json are its seed entities.
KG_STORE = KGStore(MEETING_STORE, NEWS_STORE, aliases=DATA_DIR / "companies.json")
# This system's Super Agent remembers its direct conversations, per user, in this system's schema (never another's).
MEMORY = SuperAgentMemory(MemoryStore(MEETING_STORE))
