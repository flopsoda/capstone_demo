"""Capability Registry of the Group agent system (content; mechanism in runtime.capabilities)."""

from __future__ import annotations

from ...runtime.capabilities import Capability, Registry
from .skills.subsidiary_review import SUBSIDIARY_REVIEW
from .tools.request_k_electronics_review import request_k_electronics_review

# Agent identifiers (same strings as ./agents/*.AGENT_ID and the frontend AgentId).
GROUP_SUPER = "group_super"

CAPABILITIES: tuple[Capability, ...] = (
    # Group Super
    Capability(SUBSIDIARY_REVIEW, frozenset({GROUP_SUPER})),
    Capability(request_k_electronics_review, frozenset({GROUP_SUPER})),
)

REGISTRY = Registry(CAPABILITIES)

get_capability = REGISTRY.get
capabilities_for = REGISTRY.capabilities_for
skills_for = REGISTRY.skills_for
tools_for = REGISTRY.tools_for
