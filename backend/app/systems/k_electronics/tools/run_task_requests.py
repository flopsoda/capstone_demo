"""run_task_requests — K Electronics' instance, bound to this system's meeting store (requests the Group sends over A2A)."""

from __future__ import annotations

from ....library.tools.run_task_requests import make_run_task_requests
from .. import MEETING_STORE

run_task_requests = make_run_task_requests(MEETING_STORE)
