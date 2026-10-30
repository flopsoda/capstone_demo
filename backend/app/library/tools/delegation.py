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

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ...runtime.events import current_run, emit

SKIPPED = (
    "위임하지 않음: 요청한 사용자에게 이 전문 에이전트를 쓸 권한이 없습니다. 같은 위임을 다시 시도하지 말고, "
    "이 에이전트가 맡는 항목은 결과 없이 둔 채 최종 응답에서 권한이 없어 빠졌다고 밝히세요."
)


def allowed(to_agent: str) -> bool:
    run = current_run()
    agents = run.principal.agents if run and run.principal else None
    return agents is None or to_agent in agents


class DelegationArgs(BaseModel):
    task: str = Field(description="전문 에이전트가 수행할 업무를 자연어로 서술한 문장 (한국어).")


def make_delegate_tool(
    *, name: str, description: str, from_agent: str, to_agent: str, run: Callable[[], Callable[[str], str]]
) -> BaseTool:
    """`run()` returns the specialist's `run_*_agent(task)` callable (looked up when the Tool is used)."""

    def delegate(task: str) -> str:
        if not allowed(to_agent):
            emit({"type": "delegation_skipped", "from": from_agent, "to": to_agent, "reason": "permission"})
            return SKIPPED
        emit({"type": "delegation_start", "from": from_agent, "to": to_agent, "task": task})
        response = run()(task)
        emit({"type": "delegation_end", "from": from_agent, "to": to_agent, "response": response})
        return response

    return StructuredTool.from_function(func=delegate, name=name, description=description, args_schema=DelegationArgs)
