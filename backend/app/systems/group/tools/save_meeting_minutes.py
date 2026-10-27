"""save_meeting_minutes — The Group's instance, bound to this system's meeting store."""

from __future__ import annotations

from ....library.tools.save_meeting_minutes import make_save_meeting_minutes
from .. import MEETING_STORE

save_meeting_minutes = make_save_meeting_minutes(MEETING_STORE)
