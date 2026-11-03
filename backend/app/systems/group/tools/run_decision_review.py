"""run_decision_review — The Group's instance, bound to this system's meeting store and article store (the watch job too)."""

from __future__ import annotations

from ....library.tools.run_decision_review import make_decision_watch
from .. import DATA_DIR, MEETING_STORE

DECISION_WATCH = make_decision_watch(MEETING_STORE, DATA_DIR / "news.json")
run_decision_review = DECISION_WATCH.tool
