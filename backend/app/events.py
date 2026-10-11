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
"""

from __future__ import annotations

import contextvars
import logging
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any

log = logging.getLogger("agents")

AgentEvent = dict[str, Any]

_emitter: contextvars.ContextVar[Callable[[AgentEvent], None] | None] = contextvars.ContextVar(
    "agent_event_emitter", default=None
)


class AgentError(RuntimeError):
    """An agent failed; `agent` says which one so the UI can mark it."""

    def __init__(self, agent: str, message: str):
        super().__init__(message)
        self.agent = agent


def emit(event: AgentEvent) -> None:
    sink = _emitter.get()
    if sink is not None:
        sink(event)


def collect_events(fn: Callable[[], Any]) -> tuple[Any, list[AgentEvent]]:
    """Runs `fn` synchronously and returns (result, emitted events). For tests."""
    events: list[AgentEvent] = []
    token = _emitter.set(events.append)
    try:
        return fn(), events
    finally:
        _emitter.reset(token)


_DONE = object()


def stream_run(fn: Callable[[], Any]) -> Iterator[AgentEvent]:
    """Runs `fn` in a thread and yields its events live, ending with error/done."""
    q: queue.Queue[AgentEvent | object] = queue.Queue()

    def worker() -> None:
        token = _emitter.set(q.put)
        try:
            fn()
        except AgentError as e:
            q.put({"type": "error", "agent": e.agent, "message": str(e)})
        except Exception as e:  # LLM / network failures not attributed by the agent code
            log.exception("agent run failed")
            q.put({"type": "error", "agent": "k_super", "message": f"{type(e).__name__}: {e}"})
        else:
            q.put({"type": "done"})
        finally:
            _emitter.reset(token)
            q.put(_DONE)

    threading.Thread(target=worker, daemon=True).start()
    while True:
        item = q.get()
        if item is _DONE:
            return
        yield item  # type: ignore[misc]
