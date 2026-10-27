from __future__ import annotations

from pydantic import BaseModel, Field


class Principal(BaseModel):
    """The user a run acts for, as the caller vouches for it (the gateway, after Keycloak login).
    Tools that touch user-scoped data read it from the run context, never from model arguments.
    Trusted as sent: the backends are reachable only from inside the deployment network."""

    model_config = {"frozen": True}

    email: str = Field(min_length=1)
    name: str = ""
    groups: tuple[str, ...] = ()
    clearance: int = Field(ge=0, le=4, description="0 none · 1 공개 · 2 사내 · 3 대외비 · 4 극비")


class RunRequest(BaseModel):
    request: str = Field(min_length=1, description="Natural-language request for the agent")
    principal: Principal | None = Field(default=None, description="Who the run acts for; absent for local demo UIs and tests")


class A2ARequest(RunRequest):
    """A request from another agent system. The business payload is the natural-language
    `request` only; `trace_id` / `caller` are observation metadata stamped on the run's events.
    `principal` is the end user the calling system acts for, forwarded so this system can apply
    its own access rules to its data (it is never stamped on events)."""

    trace_id: str | None = Field(default=None, description="Caller's trace id, shared by every run of one end-to-end request")
    caller: str | None = Field(default=None, description="The requesting agent system, e.g. 'group'")
