"""Execution lifecycle events emitted by the agents while they run.

Agents call `emit()` at real lifecycle points (agent start/end, delegation,
tool call). `stream_run()` runs an agent in a worker thread and yields those
events as they happen, so an HTTP handler can stream them to the UI.

Event shapes (all have "type"):
  agent_start      {agent, request}
  agent_token      {agent, text}        a piece of the agent's answer as the model writes it
  delegation_start {from, to, task}
  delegation_skipped {from, to, reason}  nothing ran: the user may not use `to` (reason permission) or `to` is up the chain (cycle)
  tool_start       {agent, tool, args}
  tool_end         {agent, tool, result}
  agent_end        {agent, response}
  delegation_end   {from, to, response}
  error            {agent, message}
  done             {}

Every event emitted inside a run is also stamped with the run's identity
(`run_id`, `system`, `origin`, optional `trace_id` / `caller`) so that
observers outside the request/response stream can tell runs — and agent
systems — apart. Fields already present on an event are kept (events relayed
from another system keep that system's stamp).

Besides the requester's stream, every event of a `stream_run` is published
to the process-wide `HUB`, which any number of observers (the Demo Monitor,
a platform UI watching for externally started runs) can subscribe to —
see `EventHub` and the SSE endpoint in api.py. A run started by another
agent system's A2A request is therefore visible in this process even though
its requester stream goes to that other system.
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

from .schemas import Principal

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
    caller: str | None = None  # for origin "a2a": the requesting agent system
    principal: Principal | None = None  # the user the run acts for; read by tools, never stamped on events
    principal_token: str | None = None  # the gateway's signature of `principal`, forwarded over A2A as received
    request: str | None = None  # for origin "a2a": the peer's request as received (tools check what it named; never stamped)

    def stamp(self, event: AgentEvent) -> AgentEvent:
        event.setdefault("run_id", self.run_id)
        event.setdefault("system", self.system)
        event.setdefault("origin", self.origin)
        if self.trace_id:
            event.setdefault("trace_id", self.trace_id)
        if self.caller:
            event.setdefault("caller", self.caller)
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


class EventHub:
    """Process-wide fan-out of run events to observers. Observers that are not
    reading fast enough simply queue up; nothing is replayed to late subscribers."""

    def __init__(self) -> None:
        self._subscribers: set[queue.Queue[AgentEvent]] = set()
        self._lock = threading.Lock()

    def publish(self, event: AgentEvent) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            q.put(event)

    def subscribe(self, *, timeout: float | None = None) -> Iterator[AgentEvent | None]:
        """Yields events as they are published; yields None when `timeout` seconds pass
        without one (so a transport can send a keep-alive). Unsubscribes when closed."""
        q: queue.Queue[AgentEvent] = queue.Queue()
        with self._lock:
            self._subscribers.add(q)
        try:
            while True:
                try:
                    yield q.get(timeout=timeout)
                except queue.Empty:
                    yield None
        finally:
            with self._lock:
                self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


HUB = EventHub()


def new_run(
    *,
    system: str,
    root_agent: str,
    origin: str = "direct",
    trace_id: str | None = None,
    caller: str | None = None,
    principal: Principal | None = None,
    principal_token: str | None = None,
    request: str | None = None,
) -> RunContext:
    return RunContext(
        run_id=uuid.uuid4().hex, system=system, root_agent=root_agent, origin=origin, trace_id=trace_id, caller=caller,
        principal=principal, principal_token=principal_token, request=request,
    )


def stream_run(fn: Callable[[], Any], *, run: RunContext, hub: EventHub | None = HUB) -> Iterator[AgentEvent]:
    """Runs `fn` in a thread and yields its events live, ending with error/done.
    Every event also goes to `hub` (the process-wide HUB by default) for observers."""
    q: queue.Queue[AgentEvent | object] = queue.Queue()

    def sink(event: AgentEvent) -> None:
        q.put(event)
        if hub is not None:
            hub.publish(event)

    def worker() -> None:
        token = _emitter.set(sink)
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
