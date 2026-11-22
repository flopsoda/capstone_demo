"""Delegation Tool factory: a Super Agent's handoff to one of its own specialist agents.

A delegation is an agent-to-agent handoff inside one system, so the Tool
reports it as delegation_start / delegation_end (not as generic tool events)
and returns only the specialist's final answer; the specialist's own messages
and tool calls never enter the caller's history.

When the run's principal lists the specialists the user may use (the gateway's
permission table), a delegation to any other specialist is not run: the Tool
reports delegation_skipped and tells the Super Agent to answer without it, so
the answer is not cut off where the gateway would otherwise stop the stream.

The Tool's name and description are the Super Agent's view of who does what,
so each system writes them (systems/<system>/tools/delegate_to_*.py) and
binds the specialist runner here. `run` is resolved at call time because the
specialist itself consumes the registry that holds this Tool (module-level
import would be circular).
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ...runtime.events import current_run, emit

SKIPPED = (
    "위임하지 않음: 요청한 사용자에게 이 전문 에이전트를 쓸 권한이 없습니다. 같은 위임을 다시 시도하지 말고, "
    "이 에이전트가 맡는 항목은 결과 없이 둔 채 최종 응답에서 권한이 없어 빠졌다고 밝히세요."
)


CYCLE = (
    "위임하지 않음: 이 에이전트는 지금 위임 사슬 안에서 이미 일하고 있습니다 (되돌아가는 위임). "
    "가진 결과로 업무를 마치세요."
)

# Agents working on the current request, outermost first (a delegation never goes back to one of them), and whether
# the work in hand was handed down as look-up only (meeting_access.current_viewer then writes nothing).
_CHAIN: ContextVar[tuple[str, ...]] = ContextVar("delegation_chain", default=())
_READ_ONLY: ContextVar[bool] = ContextVar("delegation_read_only", default=False)


def read_only() -> bool:
    return _READ_ONLY.get()


def allowed(to_agent: str) -> bool:
    run = current_run()
    agents = run.principal.agents if run and run.principal else None
    return agents is None or to_agent in agents


class DelegationArgs(BaseModel):
    task: str = Field(description="전문 에이전트가 수행할 업무를 자연어로 서술한 문장 (한국어).")


def make_delegate_tool(
    *, name: str, description: str, from_agent: str, to_agent: str, run: Callable[[], Callable[[str], str]],
    read_only: bool = False,
) -> BaseTool:
    """`run()` returns the specialist's `run_*_agent(task)` callable (looked up when the Tool is used).
    `read_only`: the specialist is asked to look records up only (a specialist consulting another one); its Tools
    write nothing for the rest of that delegation, whatever the request says."""

    def delegate(task: str) -> str:
        if not allowed(to_agent):
            emit({"type": "delegation_skipped", "from": from_agent, "to": to_agent, "reason": "permission"})
            return SKIPPED
        chain = _CHAIN.get()
        if to_agent in chain or to_agent == from_agent:
            emit({"type": "delegation_skipped", "from": from_agent, "to": to_agent, "reason": "cycle"})
            return CYCLE
        emit({"type": "delegation_start", "from": from_agent, "to": to_agent, "task": task})
        chain_token = _CHAIN.set((*(chain or (from_agent,)), to_agent))
        read_token = _READ_ONLY.set(True) if read_only else None
        try:
            response = run()(task)
        finally:
            if read_token is not None:
                _READ_ONLY.reset(read_token)
            _CHAIN.reset(chain_token)
        emit({"type": "delegation_end", "from": from_agent, "to": to_agent, "response": response})
        return response

    return StructuredTool.from_function(func=delegate, name=name, description=description, args_schema=DelegationArgs,
                                        metadata={"handoff": True, "to": to_agent, "read_only": read_only})
