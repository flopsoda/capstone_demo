"""The evidence ledger of one agent invocation: what its Tools actually returned, by source id.

Each specialist run (agent_core.run_specialist) opens a ledger; Tools that read records add what they read
(meeting decisions, action items, articles, graph facts, calculated figures) with the id the agent sees in the
result. A specialist invoked by another agent has its own ledger, and what it gathered is added to the caller's
when it returns — so the caller can cite a meeting decision its Meeting Agent read, and a check can tell an id that
was read in this run from one a model made up. The ledger is not message state: no agent's history sees it.

    with collecting() as ledger: …      one invocation's ledger (nested ones fold into the outer on exit)
    add(item) / add_many(items)         from a Tool; a no-op outside a ledger
    current()                           the open ledger, if any

An item is a dict with at least `id` and `kind`; the rest (label, date, value, unit, display, title, meeting,
classification, url, source) describes it for a report. The same id from two Tools is one item with two sources.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Ledger:
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    analyses: list[dict[str, Any]] = field(default_factory=list)  # full results of the numbers Tool, in order

    def add(self, item: dict[str, Any]) -> None:
        id_ = str(item.get("id") or "").strip()
        if not id_:
            return
        known = self.items.get(id_)
        if known is None:
            self.items[id_] = {**item, "id": id_, "sources": [item["source"]] if item.get("source") else []}
            return
        for k, v in item.items():  # a second source of the same record: keep what the first lacked
            if k != "source" and known.get(k) in (None, "", []) and v not in (None, "", []):
                known[k] = v
        if item.get("source") and item["source"] not in known["sources"]:
            known["sources"].append(item["source"])

    def merge(self, other: Ledger) -> None:
        for item in other.items.values():
            for source in item.get("sources") or [None]:
                self.add({**item, "source": source})
        self.analyses.extend(a for a in other.analyses if a not in self.analyses)

    def get(self, id_: str) -> dict[str, Any] | None:
        return self.items.get(id_)


_LEDGER: ContextVar[Ledger | None] = ContextVar("evidence_ledger", default=None)


def current() -> Ledger | None:
    return _LEDGER.get()


@contextmanager
def collecting() -> Iterator[Ledger]:
    outer = _LEDGER.get()
    ledger = Ledger()
    token = _LEDGER.set(ledger)
    try:
        yield ledger
    finally:
        _LEDGER.reset(token)
        if outer is not None:
            outer.merge(ledger)


def add(item: dict[str, Any]) -> None:
    ledger = _LEDGER.get()
    if ledger is not None:
        ledger.add(item)


def add_many(items: Iterable[dict[str, Any]]) -> None:
    ledger = _LEDGER.get()
    if ledger is not None:
        for item in items:
            ledger.add(item)
