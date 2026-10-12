"""Group agent system (the holding group's platform).

    app.py       FastAPI app: `uvicorn app.systems.group.app:app --port 8001`
    registry.py  this system's CAPABILITIES with permissions
    agents/      group_super (super core)
    skills/      Skills specific to this system (library Skills are registered from app/library)
    tools/       request_k_electronics_review (A2A client to the K Electronics system)
    data/        this system's fixtures (virtual)

Talks to K Electronics only through its A2A surface (HTTP); never imports it.
"""

from __future__ import annotations

from pathlib import Path

SYSTEM = "group"  # stamped on every event this system emits
DATA_DIR = Path(__file__).resolve().parent / "data"
