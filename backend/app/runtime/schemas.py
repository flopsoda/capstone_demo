from __future__ import annotations

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    request: str = Field(min_length=1, description="Natural-language request for the agent")


class A2ARequest(RunRequest):
    """A request from another agent system. The business payload is the natural-language
    `request` only; `trace_id` / `caller` are observation metadata stamped on the run's events."""

    trace_id: str | None = Field(default=None, description="Caller's trace id, shared by every run of one end-to-end request")
    caller: str | None = Field(default=None, description="The requesting agent system, e.g. 'group'")
