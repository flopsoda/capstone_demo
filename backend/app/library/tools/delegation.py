"""Delegation Tool factory: a Super Agent's handoff to one of its own specialist agents.

A delegation is an agent-to-agent handoff inside one system, so the Tool
reports it as delegation_start / delegation_end (not as generic tool events)
and returns only the specialist's final answer; the specialist's own messages
and tool calls never enter the caller's history.

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

from ...runtime.events import emit


class DelegationArgs(BaseModel):
    task: str = Field(description="전문 에이전트가 수행할 업무를 자연어로 서술한 문장 (한국어).")


def make_delegate_tool(
    *, name: str, description: str, from_agent: str, to_agent: str, run: Callable[[], Callable[[str], str]]
) -> BaseTool:
    """`run()` returns the specialist's `run_*_agent(task)` callable (looked up when the Tool is used)."""

    def delegate(task: str) -> str:
        emit({"type": "delegation_start", "from": from_agent, "to": to_agent, "task": task})
        response = run()(task)
        emit({"type": "delegation_end", "from": from_agent, "to": to_agent, "response": response})
        return response

    return StructuredTool.from_function(func=delegate, name=name, description=description, args_schema=DelegationArgs)
