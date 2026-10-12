"""run_meeting_context_retrieval — K Electronics' instance, bound to this system's data/meetings.json."""

from __future__ import annotations

from ....library.tools.run_meeting_context_retrieval import make_run_meeting_context_retrieval
from .. import DATA_DIR

run_meeting_context_retrieval = make_run_meeting_context_retrieval(DATA_DIR / "meetings.json")
