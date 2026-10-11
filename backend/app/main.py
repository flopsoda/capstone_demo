"""Minimal API layer between the React demo and the LangGraph agents."""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException

from .agents.k_super import NoDelegationError, run_k_super
from .agents.meeting import run_meeting_agent
from .llm import LLMConfigError
from .schemas import AgentRunOut, KSuperRunOut, RunRequest

log = logging.getLogger("agents")

app = FastAPI(title="capstone agents backend", version="0.1.0")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/k-super/run", response_model=KSuperRunOut)
def k_super_run(body: RunRequest) -> KSuperRunOut:
    """Scenario Step 3: K Super receives the A2A request and delegates."""
    try:
        delegations = run_k_super(body.request)
    except LLMConfigError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except NoDelegationError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:  # LLM / network failures
        log.exception("k_super run failed")
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e
    return KSuperRunOut(delegations=delegations)  # type: ignore[arg-type]


@app.post("/api/agents/meeting/run", response_model=AgentRunOut)
def meeting_run(body: RunRequest) -> AgentRunOut:
    """Direct user → Meeting Agent (sidebar chat), same agent core as Step 3."""
    try:
        run = run_meeting_agent(body.request)
    except LLMConfigError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        log.exception("meeting run failed")
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e
    return AgentRunOut(**run)
