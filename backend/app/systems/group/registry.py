"""Capability Registry of the Group agent system (content; mechanism in runtime.capabilities).

Group Super has two kinds of handoff Tools: delegation to this system's own
specialists and an A2A request to the K Electronics system. Nothing here
steers the model toward one or the other; it decides from the request and
the Tools' descriptions.
"""

from __future__ import annotations

from ...library.skills.external_risk_analysis import EXTERNAL_RISK_ANALYSIS
from ...library.skills.meeting_context_analysis import MEETING_CONTEXT_ANALYSIS
from ...library.skills.mna_feasibility_review import MNA_FEASIBILITY_REVIEW
from ...library.skills.specialist_delegation import SPECIALIST_DELEGATION
from ...runtime.capabilities import Capability, Registry
from .skills.subsidiary_review import SUBSIDIARY_REVIEW
from .tools.delegate_to_advisory_agent import delegate_to_advisory_agent
from .tools.delegate_to_meeting_agent import delegate_to_meeting_agent
from .tools.delegate_to_news_agent import delegate_to_news_agent
from .tools.request_k_electronics_review import request_k_electronics_review
from .tools.run_company_risk_news_analysis import run_company_risk_news_analysis
from .tools.run_meeting_context_retrieval import run_meeting_context_retrieval
from .tools.run_mna_review import run_mna_review

# Agent identifiers (same strings as ./agents/*.AGENT_ID and the frontend AgentId).
GROUP_SUPER = "group_super"
MEETING = "meeting"
NEWS = "news"
ADVISORY = "advisory"

CAPABILITIES: tuple[Capability, ...] = (
    # Group Super
    Capability(SUBSIDIARY_REVIEW, frozenset({GROUP_SUPER})),
    Capability(SPECIALIST_DELEGATION, frozenset({GROUP_SUPER})),
    Capability(request_k_electronics_review, frozenset({GROUP_SUPER})),
    Capability(delegate_to_meeting_agent, frozenset({GROUP_SUPER})),
    Capability(delegate_to_news_agent, frozenset({GROUP_SUPER})),
    Capability(delegate_to_advisory_agent, frozenset({GROUP_SUPER})),
    # Meeting Agent (Group's meeting store)
    Capability(MEETING_CONTEXT_ANALYSIS, frozenset({MEETING})),
    Capability(run_meeting_context_retrieval, frozenset({MEETING})),
    # News Agent (Group's article store)
    Capability(EXTERNAL_RISK_ANALYSIS, frozenset({NEWS})),
    Capability(run_company_risk_news_analysis, frozenset({NEWS})),
    # Management Advisory Agent (Group's company store)
    Capability(MNA_FEASIBILITY_REVIEW, frozenset({ADVISORY})),
    Capability(run_mna_review, frozenset({ADVISORY})),
)

REGISTRY = Registry(CAPABILITIES)

get_capability = REGISTRY.get
capabilities_for = REGISTRY.capabilities_for
skills_for = REGISTRY.skills_for
tools_for = REGISTRY.tools_for
