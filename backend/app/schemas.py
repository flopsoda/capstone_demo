from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    request: str = Field(min_length=1, description="Natural-language request for the agent")


class ToolCallOut(BaseModel):
    name: str
    args: dict[str, Any]
    result: Any = None


class AgentRunOut(BaseModel):
    agent: str
    request: str
    tool_calls: list[ToolCallOut]
    response: str


class KSuperRunOut(BaseModel):
    """Trace of one K Super run: one entry per specialist-agent delegation."""

    delegations: list[AgentRunOut]
