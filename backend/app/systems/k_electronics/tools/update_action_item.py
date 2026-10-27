"""update_action_item — K Electronics' instance, bound to this system's meeting store."""

from __future__ import annotations

from ....library.tools.update_action_item import make_update_action_item
from .. import MEETING_STORE

update_action_item = make_update_action_item(MEETING_STORE)
