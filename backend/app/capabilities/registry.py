"""Capability Registry of the K Electronics agent system — the single place that answers:

    which capabilities exist?  skill or tool?  which agents may use each?  what implements it?

Implementations live in ./skills and ./tools; this module only lists them and
attaches permissions (the registry *content*; the lookup mechanism is
base.Registry). Agents ask for their own subset with skills_for() /
tools_for() and never import skill or tool modules directly.

Internal helpers and the steps inside a tool's workflow (app/workflows/) are
not capabilities and are not registered.
"""

from __future__ import annotations

from .base import Capability, Registry
from .skills.external_risk_analysis import EXTERNAL_RISK_ANALYSIS
from .skills.meeting_context_analysis import MEETING_CONTEXT_ANALYSIS
from .skills.mna_feasibility_review import MNA_FEASIBILITY_REVIEW
from .skills.specialist_delegation import SPECIALIST_DELEGATION
from .tools.delegate_to_advisory_agent import delegate_to_advisory_agent
from .tools.delegate_to_meeting_agent import delegate_to_meeting_agent
from .tools.delegate_to_news_agent import delegate_to_news_agent
from .tools.run_company_risk_news_analysis import run_company_risk_news_analysis
from .tools.run_meeting_context_retrieval import run_meeting_context_retrieval
from .tools.run_mna_review import run_mna_review

# Agent identifiers (same strings as app/agents/*.AGENT_ID and the frontend AgentId).
K_SUPER = "k_super"
MEETING = "meeting"
NEWS = "news"
ADVISORY = "advisory"

CAPABILITIES: tuple[Capability, ...] = (
    # K Electronics Super
    Capability(SPECIALIST_DELEGATION, frozenset({K_SUPER})),
    Capability(delegate_to_meeting_agent, frozenset({K_SUPER})),
    Capability(delegate_to_news_agent, frozenset({K_SUPER})),
    Capability(delegate_to_advisory_agent, frozenset({K_SUPER})),
    # Meeting Agent
    Capability(MEETING_CONTEXT_ANALYSIS, frozenset({MEETING})),
    Capability(run_meeting_context_retrieval, frozenset({MEETING})),
    # News Agent
    Capability(EXTERNAL_RISK_ANALYSIS, frozenset({NEWS})),
    Capability(run_company_risk_news_analysis, frozenset({NEWS})),
    # Management Advisory Agent
    Capability(MNA_FEASIBILITY_REVIEW, frozenset({ADVISORY})),
    Capability(run_mna_review, frozenset({ADVISORY})),
)

REGISTRY = Registry(CAPABILITIES)

get_capability = REGISTRY.get
capabilities_for = REGISTRY.capabilities_for
skills_for = REGISTRY.skills_for
tools_for = REGISTRY.tools_for
