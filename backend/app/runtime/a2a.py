"""A2A client mechanism: send a business request to another agent system's
Super Agent and get its final answer back.

Wire format is the peer's A2A inbound surface (POST, NDJSON lifecycle stream,
see systems/*/app.py): the peer's answer is the `response` of the last
`agent_end` before `done`; an `error` event is the peer's failure.

The lifecycle events in between are *observation* data. The caller may pass
them to `observe` (the Group system relays them into its own run so its UI can
show the peer's internal sessions) but must not feed them to its agent: the
agent gets only the returned answer.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from .events import AgentEvent

A2A_TIMEOUT = httpx.Timeout(connect=5.0, read=600.0, write=10.0, pool=5.0)


class A2AError(RuntimeError):
    """The peer system failed (its own `error` event) or could not be reached."""

    def __init__(self, peer_agent: str | None, message: str):
        super().__init__(message)
        self.peer_agent = peer_agent  # the agent the peer blamed, if it got that far


def request_review(
    client: httpx.Client,
    url: str,
    request: str,
    *,
    caller: str,
    trace_id: str | None,
    observe: Callable[[AgentEvent], None] | None = None,
) -> str:
    """Posts `request` to the peer's A2A surface and returns the peer's final answer."""
    body: dict[str, Any] = {"request": request, "trace_id": trace_id, "caller": caller}
    final: str | None = None
    finished = False
    try:
        with client.stream("POST", url, json=body, headers={"Accept": "application/x-ndjson"}) as res:
            if res.status_code != 200:
                raise A2AError(None, f"A2A 요청이 거부되었습니다 (HTTP {res.status_code})")
            for line in res.iter_lines():
                if not line.strip():
                    continue
                event = json.loads(line)
                kind = event.get("type")
                if kind == "error":
                    raise A2AError(event.get("agent"), str(event.get("message", "")))
                if kind == "done":
                    finished = True
                    break
                if kind == "agent_end":
                    final = str(event.get("response", ""))
                if observe is not None:
                    observe(event)
    except httpx.HTTPError as e:
        raise A2AError(None, f"상대 Agent System에 연결할 수 없습니다: {type(e).__name__}: {e}") from e
    if not finished:
        raise A2AError(None, "상대 Agent System의 스트림이 완료 신호 없이 끝났습니다.")
    if not final:
        raise A2AError(None, "상대 Agent System이 최종 응답을 보내지 않았습니다.")
    return final
