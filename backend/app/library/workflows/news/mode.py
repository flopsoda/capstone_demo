"""Which news a system serves: real news (live, the default) or the demo scenario's fictional
articles (demo). Set per system process with NEWS_DATA_MODE=live|demo; never a model argument.

The two never mix: live runs read only real sources and the news database, demo runs read only
the system's fixture (data/news.json) — offline, on the scenario's own calendar.
"""

from __future__ import annotations

import os

LIVE = "live"
DEMO = "demo"


def data_mode() -> str:
    value = os.environ.get("NEWS_DATA_MODE", "").strip().lower()
    return DEMO if value == DEMO else LIVE
