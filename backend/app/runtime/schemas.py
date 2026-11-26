from __future__ import annotations

from pydantic import BaseModel, Field


class Principal(BaseModel):
    """The user a run acts for, as the caller vouches for it (the gateway, after Keycloak login).
    Tools that touch user-scoped data read it from the run context, never from model arguments.
    In deployment it arrives signed (`principal_token`, runtime/principal.py); the plain field is
    used only where no signing key is configured (local demo UIs, tests)."""

    model_config = {"frozen": True}

    email: str = Field(min_length=1)
    name: str = ""
    groups: tuple[str, ...] = ()
    clearance: int = Field(ge=0, le=4, description="0 none · 1 공개 · 2 사내 · 3 대외비 · 4 극비")
    agents: tuple[str, ...] | None = Field(
        default=None, description="Specialist ids (meeting · news · advisory) the user may use, from the gateway's permission table; None = not restricted")
    user_id: str | None = Field(default=None, description="The user's Keycloak id when the gateway knows it (meeting check-in: whose voiceprint)")


class Identified(BaseModel):
    """Who a request acts for (runs, and the system's non-agent endpoints such as the meeting desk)."""

    principal: Principal | None = Field(default=None, description="Who the run acts for; absent for local demo UIs and tests")
    principal_token: str | None = Field(default=None, description="The principal signed by the gateway (required when PRINCIPAL_SIGNING_KEY is set)")


class ReportRequest(Identified):
    """Open a report a Tool result referred to (`artifacts`): which system keeps it and its id."""

    id: str = Field(min_length=1, description="The report's id (a kept Advisory judgement, e.g. ADV-2026-11-22-01)")
    system: str | None = Field(default=None, description="The agent system that keeps it; absent = this system")


class RunRequest(Identified):
    request: str = Field(min_length=1, description="Natural-language request for the agent")


class A2ARequest(RunRequest):
    """A request from another agent system. The business payload is the natural-language
    `request` only; `trace_id` / `caller` are observation metadata stamped on the run's events.
    `principal` / `principal_token` is the end user the calling system acts for, forwarded so this
    system can apply its own access rules to its data (it is never stamped on events)."""

    trace_id: str | None = Field(default=None, description="Caller's trace id, shared by every run of one end-to-end request")
    caller: str | None = Field(default=None, description="The requesting agent system, e.g. 'group'")
