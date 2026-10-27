"""run_meeting_schedule — K Electronics' instance, bound to this system's meeting store."""

from __future__ import annotations

from ....library.tools.run_meeting_schedule import make_run_meeting_schedule
from .. import MEETING_STORE

run_meeting_schedule = make_run_meeting_schedule(MEETING_STORE)
