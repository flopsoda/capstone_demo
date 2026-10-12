"""Agent Capabilities: Skills (playbooks) and Tools (callables), with per-agent permissions.

    capabilities/
      base.py        Skill / Capability types + Registry lookup mechanism
      registry.py    this system's CAPABILITIES (source of truth for permissions) + lookups
      skills/        Skill definitions
      tools/         @tool definitions (agent-visible surface over app/workflows/)
"""

from .base import Capability, CapabilityKind, Registry, Skill
from .registry import CAPABILITIES, capabilities_for, get_capability, skills_for, tools_for

__all__ = [
    "CAPABILITIES",
    "Capability",
    "CapabilityKind",
    "Registry",
    "Skill",
    "capabilities_for",
    "get_capability",
    "skills_for",
    "tools_for",
]
