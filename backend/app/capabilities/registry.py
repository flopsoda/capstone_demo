"""Capability Registry — the single place that answers:

    which capabilities exist?  skill or tool?  which agents may use each?  what implements it?

Implementations live in ./skills and ./tools; this module only lists them and
attaches permissions. Agents ask for their own subset with skills_for() /
tools_for() and never import skill or tool modules directly.

Internal helpers and the steps inside a tool's workflow (app/workflows/) are
not capabilities and are not registered.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from .base import Capability, Skill
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

_BY_NAME: dict[str, Capability] = {}
for _c in CAPABILITIES:
    if _c.name in _BY_NAME:
        raise ValueError(f"duplicate capability name: {_c.name}")
    if not _c.allowed_agents:
        raise ValueError(f"capability {_c.name} has no allowed agents")
    _BY_NAME[_c.name] = _c


def get_capability(name: str) -> Capability:
    return _BY_NAME[name]


def capabilities_for(agent_id: str) -> list[Capability]:
    return [c for c in CAPABILITIES if c.allows(agent_id)]


def skills_for(agent_id: str) -> list[Skill]:
    return [c.impl for c in capabilities_for(agent_id) if isinstance(c.impl, Skill)]


def tools_for(agent_id: str) -> list[BaseTool]:
    return [c.impl for c in capabilities_for(agent_id) if isinstance(c.impl, BaseTool)]
