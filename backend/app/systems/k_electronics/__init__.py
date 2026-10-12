"""K Electronics agent system.

    app.py       FastAPI app (uvicorn app.main:app → this)
    registry.py  this system's CAPABILITIES with permissions
    agents/      k_super (super core) · meeting / news / advisory (specialist core)
    tools/       delegate_to_* (handoffs to this system's specialists) · run_* bound to data/
    data/        this system's fixtures (virtual)
"""

from __future__ import annotations

from pathlib import Path

SYSTEM = "k_electronics"  # stamped on every event this system emits
DATA_DIR = Path(__file__).resolve().parent / "data"
