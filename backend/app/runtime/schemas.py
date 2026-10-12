from __future__ import annotations

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    request: str = Field(min_length=1, description="Natural-language request for the agent")
