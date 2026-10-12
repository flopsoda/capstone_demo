"""Execution lifecycle events emitted by the agents while they run.

Agents call `emit()` at real lifecycle points (agent start/end, delegation,
tool call). `stream_run()` runs an agent in a worker thread and yields those
events as they happen, so an HTTP handler can stream them to the UI.

Event shapes (all have "type"):
  agent_start      {agent, request}
  delegation_start {from, to, task}
  tool_start       {agent, tool, args}
  tool_end         {agent, tool, result}
  agent_end        {agent, response}
  delegation_end   {from, to, response}
  error            {agent, message}
  done             {}

Every event emitted inside a run is also stamped with the run's identity
(`run_id`, `system`, `origin`, optional `trace_id`) so that observers outside
the request/response stream can tell runs — and agent systems — apart. Fields
already present on an event are kept (events relayed from another system
keep that system's stamp).
"""

from __future__ import annotations

import contextvars
import logging
import queue
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("agents")

AgentEvent = dict[str, Any]


@dataclass(frozen=True)
class RunContext:
    """Identity of one agent run, stamped on every event it emits."""

    run_id: str
    system: str  # the agent system this process belongs to (e.g. "k_electronics")
    root_agent: str  # the agent the run was started on; failures not attributed otherwise go here
    origin: str  # what started the run: "direct" (user) or "a2a" (another agent system)
    trace_id: str | None = None  # shared by runs that belong to one end-to-end request

    def stamp(self, event: AgentEvent) -> AgentEvent:
        event.setdefault("run_id", self.run_id)
        event.setdefault("system", self.system)
        event.setdefault("origin", self.origin)
        if self.trace_id:
            event.setdefault("trace_id", self.trace_id)
        return event


_emitter: contextvars.ContextVar[Callable[[AgentEvent], None] | None] = contextvars.ContextVar(
    "agent_event_emitter", default=None
)
_run: contextvars.ContextVar[RunContext | None] = contextvars.ContextVar("agent_run_context", default=None)


class AgentError(RuntimeError):
    """An agent failed; `agent` says which one so the UI can mark it."""

    def __init__(self, agent: str, message: str):
        super().__init__(message)
        self.agent = agent


def current_run() -> RunContext | None:
    return _run.get()


def emit(event: AgentEvent) -> None:
    sink = _emitter.get()
    if sink is None:
        return
    ctx = _run.get()
    sink(ctx.stamp(event) if ctx is not None else event)


def collect_events(fn: Callable[[], Any]) -> tuple[Any, list[AgentEvent]]:
    """Runs `fn` synchronously and returns (result, emitted events). For tests; no run stamp."""
    events: list[AgentEvent] = []
    token = _emitter.set(events.append)
    try:
        return fn(), events
    finally:
        _emitter.reset(token)


_DONE = object()


def new_run(*, system: str, root_agent: str, origin: str = "direct", trace_id: str | None = None) -> RunContext:
    return RunContext(run_id=uuid.uuid4().hex, system=system, root_agent=root_agent, origin=origin, trace_id=trace_id)


def stream_run(fn: Callable[[], Any], *, run: RunContext) -> Iterator[AgentEvent]:
    """Runs `fn` in a thread and yields its events live, ending with error/done."""
    q: queue.Queue[AgentEvent | object] = queue.Queue()

    def worker() -> None:
        token = _emitter.set(q.put)
        run_token = _run.set(run)
        try:
            fn()
        except AgentError as e:
            emit({"type": "error", "agent": e.agent, "message": str(e)})
        except Exception as e:  # LLM / network failures not attributed by the agent code
            log.exception("agent run failed")
            emit({"type": "error", "agent": run.root_agent, "message": f"{type(e).__name__}: {e}"})
        else:
            emit({"type": "done"})
        finally:
            _run.reset(run_token)
            _emitter.reset(token)
            q.put(_DONE)

    threading.Thread(target=worker, daemon=True).start()
    while True:
        item = q.get()
        if item is _DONE:
            return
        yield item  # type: ignore[misc]
