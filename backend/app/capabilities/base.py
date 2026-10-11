"""Capability types.

A Capability is something an agent may use. Two kinds exist:

  Skill  — a playbook: instructions telling the agent how to carry out a kind
           of work. Rendered into the agent's system context.
  Tool   — an executable the model can call. Represented directly by the
           LangChain BaseTool, which stays the source of truth for the name,
           description and argument schema.

This module has no dependency on agents or on the registry so that skill and
tool modules can import it freely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from langchain_core.tools import BaseTool

CapabilityKind = Literal["skill", "tool"]


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    instructions: str  # the playbook text that goes into the agent's system context


@dataclass(frozen=True)
class Capability:
    """A registry entry: the implementation plus which agents may use it."""

    impl: Skill | BaseTool
    allowed_agents: frozenset[str]

    @property
    def kind(self) -> CapabilityKind:
        return "skill" if isinstance(self.impl, Skill) else "tool"

    @property
    def name(self) -> str:
        return self.impl.name

    @property
    def description(self) -> str:
        return self.impl.description

    def allows(self, agent_id: str) -> bool:
        return agent_id in self.allowed_agents
