"""Conversation memory of a Super Agent — what the runtime asks of one (the storage is the library's, bound by a system).

A Super Agent with a memory (agent_core.SuperAgent.memory) remembers its direct conversations with a user and,
when a new request comes, recalls the past exchanges that bear on it:

    run_super(direct) → recall(run, request) → the paragraph goes into the system context of every model turn
                      → model ⇄ handoffs (delegations, A2A — unchanged)
                      → agent_end → remember(run, request, response, handoffs)

The recall is reported with the existing tool events (tool_start / tool_end, tool RECALL_TOOL, args.auto = true),
like the transcript correction step: it is not a Tool the model calls. A2A runs neither recall nor remember.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .events import RunContext

RECALL_TOOL = "recall_conversation_memory"


@dataclass(frozen=True)
class Recall:
    context: str  # the paragraph for the system context ("" when nothing relevant was found)
    result: dict[str, Any] = field(default_factory=dict)  # what the recall found, as the tool_end result


class ConversationMemory(Protocol):
    def applies(self, run: RunContext) -> bool:
        """Whether this run's conversation is remembered (a known user, a direct run, a configured store)."""

    def recall(self, run: RunContext, request: str) -> Recall:
        """Past exchanges of the run's user that bear on `request`."""

    def remember(self, run: RunContext, request: str, response: str, handoffs: list[dict[str, Any]]) -> None:
        """Keeps the exchange (once per run) and what the Super Agent handed off for it."""
