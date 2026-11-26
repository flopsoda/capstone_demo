"""Shared plumbing of the meeting Tools: who the call acts for, and store failures as results.

The viewer comes from the run (the principal the caller vouched for, and whether another
agent system started the run), never from the model's arguments, so a model cannot widen
its own access. A failure the model can act on (no store configured, a constraint the input
broke) comes back as {"error": ...} instead of ending the run.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import psycopg

from ...runtime.events import current_run
from ..workflows.meeting_store import StoreUnavailable, Viewer, viewer_for
from .delegation import read_only


def current_viewer() -> Viewer:
    """The run's user; under a read-only delegation (another specialist asked only to look records up) nothing is
    written — not even what an A2A request may do."""
    run = current_run()
    viewer = viewer_for(run.principal if run else None, run.origin if run else None)
    return replace(viewer, can_write=False, peer=False) if read_only() else viewer


def plain(value: Any) -> Any:
    """Tool arguments → plain dicts and lists (nested argument models arrive as pydantic objects)."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def store_call(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return fn()
    except StoreUnavailable as e:
        return {"error": str(e)}
    except (psycopg.errors.IntegrityError, psycopg.errors.DataError) as e:
        return {"error": f"저장할 수 없는 값입니다: {e.diag.message_primary or e}"}
