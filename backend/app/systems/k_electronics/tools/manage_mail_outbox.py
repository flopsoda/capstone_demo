"""manage_mail_outbox — K Electronics' instance, bound to this system's meeting store."""

from __future__ import annotations

from ....library.tools.manage_mail_outbox import make_manage_mail_outbox
from .. import MEETING_STORE

manage_mail_outbox = make_manage_mail_outbox(MEETING_STORE)
