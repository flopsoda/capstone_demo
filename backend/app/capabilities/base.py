"""Capability types and the registry mechanism.

A Capability is something an agent may use. Two kinds exist:

  Skill  — a playbook: instructions telling the agent how to carry out a kind
           of work. Rendered into the agent's system context.
  Tool   — an executable the model can call. Represented directly by the
           LangChain BaseTool, which stays the source of truth for the name,
           description and argument schema.

A Registry is the mechanism that answers "which capabilities exist, skill or
tool, which agents may use each, what implements it" for one agent system.
The *content* of a registry (the Capability tuple with permissions) belongs
to the agent system that owns it (see registry.py); this module only
provides the types and lookup, and has no dependency on agents or on any
registry content so that skill and tool modules can import it freely.
"""

from __future__ import annotations

from collections.abc import Iterable
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


class Registry:
    """Capability lookup for one agent system. Rejects duplicate names and empty permissions."""

    def __init__(self, capabilities: Iterable[Capability]):
        self.capabilities: tuple[Capability, ...] = tuple(capabilities)
        self._by_name: dict[str, Capability] = {}
        for c in self.capabilities:
            if c.name in self._by_name:
                raise ValueError(f"duplicate capability name: {c.name}")
            if not c.allowed_agents:
                raise ValueError(f"capability {c.name} has no allowed agents")
            self._by_name[c.name] = c

    def get(self, name: str) -> Capability:
        return self._by_name[name]

    def capabilities_for(self, agent_id: str) -> list[Capability]:
        return [c for c in self.capabilities if c.allows(agent_id)]

    def skills_for(self, agent_id: str) -> list[Skill]:
        return [c.impl for c in self.capabilities_for(agent_id) if isinstance(c.impl, Skill)]

    def tools_for(self, agent_id: str) -> list[BaseTool]:
        return [c.impl for c in self.capabilities_for(agent_id) if isinstance(c.impl, BaseTool)]
