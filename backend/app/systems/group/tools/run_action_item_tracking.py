"""run_action_item_tracking — The Group's instance, bound to this system's meeting store."""

from __future__ import annotations

from ....library.tools.run_action_item_tracking import make_run_action_item_tracking
from .. import MEETING_STORE

run_action_item_tracking = make_run_action_item_tracking(MEETING_STORE)
