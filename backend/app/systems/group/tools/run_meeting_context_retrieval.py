"""run_meeting_context_retrieval — The Group's instance, bound to this system's meeting store."""

from __future__ import annotations

from ....library.tools.run_meeting_context_retrieval import make_run_meeting_context_retrieval
from .. import MEETING_STORE

run_meeting_context_retrieval = make_run_meeting_context_retrieval(MEETING_STORE)
