"""Background jobs of one system's meeting store, run by its app (runtime.api.shutdown_signal on_start).

    every MEETINGS_DELIVERY_INTERVAL seconds (60)   send queued mail (outbox.deliver_approved)
    once a day from MEETINGS_DAILY_HOUR KST (9)     queue due-date reminders, look up addresses still missing,
                                                    delete expired transcripts

Started only when MEETINGS_BACKGROUND_JOBS=1 (deployment) and the store is configured: tests and
local runs never write on their own. Each pass takes a per-schema advisory lock, so two processes
on one schema do not both send. Not agent-visible; no LangChain dependency.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime

from .meeting_store import KST, MeetingStore, StoreUnavailable, today_kst
from .outbox import deliver_approved, queue_reminders, retry_addresses
from .transcripts import purge_expired

log = logging.getLogger("agents")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def run_pass(store: MeetingStore, *, daily: bool) -> dict[str, int]:
    """One pass (also what the tests call): {sent, reminders, addressed, purged}, or {} if another process holds the lock."""
    out = {"sent": 0, "reminders": 0, "addressed": 0, "purged": 0}
    with store.connect() as conn:
        if not conn.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS ok", (f"{store.schema}:jobs",)).fetchone()["ok"]:
            return {}
        if daily:
            out["reminders"] = queue_reminders(conn, today_kst())
            out["addressed"] = retry_addresses(conn)
            out["purged"] = purge_expired(conn)
    # Sending in its own transaction, so the reminders queued above are committed first and go out in this pass.
    with store.connect() as conn:
        if conn.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS ok", (f"{store.schema}:jobs",)).fetchone()["ok"]:
            out["sent"] = deliver_approved(conn)
    return out


def start_meeting_jobs(store: MeetingStore, stop: threading.Event) -> threading.Thread | None:
    if os.environ.get("MEETINGS_BACKGROUND_JOBS", "0") != "1" or not os.environ.get("MEETINGS_DATABASE_URL"):
        return None
    interval = max(10, _int_env("MEETINGS_DELIVERY_INTERVAL", 60))
    daily_hour = min(23, max(0, _int_env("MEETINGS_DAILY_HOUR", 9)))

    def loop() -> None:
        last_daily = None
        while not stop.is_set():
            now = datetime.now(KST)
            daily = now.hour >= daily_hour and last_daily != now.date()
            try:
                out = run_pass(store, daily=daily)
                if daily and out:
                    last_daily = now.date()
                if any(out.values()):
                    log.info("meeting jobs (%s): %s", store.schema, out)
            except StoreUnavailable as e:
                log.warning("meeting jobs (%s): %s", store.schema, e)
            except Exception:  # keep the loop alive; the next pass retries
                log.exception("meeting jobs (%s) failed", store.schema)
            stop.wait(interval)

    thread = threading.Thread(target=loop, name=f"meeting-jobs-{store.schema}", daemon=True)
    thread.start()
    log.info("meeting jobs (%s) started: every %ss, daily from %02d:00 KST", store.schema, interval, daily_hour)
    return thread
