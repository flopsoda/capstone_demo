"""Capability Registry of the K Electronics agent system — the single place that answers:

    which capabilities exist?  skill or tool?  which agents may use each?  what implements it?

Skill texts and Tool factories live in app/library; this system's Tool
instances (bound to its data) in ./tools. This module only lists them and
attaches permissions (the registry *content*; the lookup mechanism is
runtime.capabilities.Registry). Agents ask for their own subset with skills_for() /
tools_for() and never import skill or tool modules directly.

Internal helpers and the steps inside a tool's workflow (app/library/workflows/) are
not capabilities and are not registered. Group's agents are not in this registry:
the Group system has its own.
"""

from __future__ import annotations

from ...library.skills.enterprise_knowledge import ENTERPRISE_KNOWLEDGE
from ...library.skills.external_risk_analysis import EXTERNAL_RISK_ANALYSIS
from ...library.skills.inbound_task_requests import INBOUND_TASK_REQUESTS
from ...library.skills.news_briefing import NEWS_BRIEFING
from ...library.skills.meeting_context_analysis import MEETING_CONTEXT_ANALYSIS
from ...library.skills.meeting_follow_up import MEETING_FOLLOW_UP
from ...library.skills.meeting_minutes_writing import MEETING_MINUTES_WRITING
from ...library.skills.mna_feasibility_review import MNA_FEASIBILITY_REVIEW
from ...library.skills.specialist_delegation import SPECIALIST_DELEGATION
from ...runtime.capabilities import Capability, Registry
from .tools.delegate_to_advisory_agent import delegate_to_advisory_agent
from .tools.delegate_to_meeting_agent import delegate_to_meeting_agent
from .tools.delegate_to_news_agent import delegate_to_news_agent
from .tools.manage_mail_outbox import manage_mail_outbox
from .tools.run_company_risk_news_analysis import run_company_risk_news_analysis
from .tools.run_news_briefing import run_news_briefing
from .tools.run_action_item_tracking import run_action_item_tracking
from .tools.run_meeting_context_retrieval import run_meeting_context_retrieval
from .tools.run_meeting_schedule import run_meeting_schedule
from .tools.advisory_reviews import add_advisory_details, lookup_advisory_reviews, record_advisory_opinion
from .tools.ask_meeting_agent import ask_meeting_agent
from .tools.ask_news_agent import ask_news_agent
from .tools.query_enterprise_kg import query_enterprise_kg
from .tools.run_mna_review import run_mna_review
from .tools.run_task_requests import run_task_requests
from .tools.save_meeting_minutes import save_meeting_minutes
from .tools.update_action_item import update_action_item

# Agent identifiers (same strings as ./agents/*.AGENT_ID and the frontend AgentId).
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
    Capability(MEETING_MINUTES_WRITING, frozenset({MEETING})),
    Capability(MEETING_FOLLOW_UP, frozenset({MEETING})),
    Capability(INBOUND_TASK_REQUESTS, frozenset({MEETING})),
    Capability(run_meeting_context_retrieval, frozenset({MEETING})),
    Capability(save_meeting_minutes, frozenset({MEETING})),
    Capability(run_action_item_tracking, frozenset({MEETING})),
    Capability(update_action_item, frozenset({MEETING})),
    Capability(run_meeting_schedule, frozenset({MEETING})),
    Capability(manage_mail_outbox, frozenset({MEETING})),
    Capability(run_task_requests, frozenset({MEETING})),
    # News Agent
    Capability(EXTERNAL_RISK_ANALYSIS, frozenset({NEWS})),
    Capability(NEWS_BRIEFING, frozenset({NEWS})),
    Capability(run_news_briefing, frozenset({NEWS})),
    Capability(run_company_risk_news_analysis, frozenset({NEWS})),
    # Management Advisory Agent
    Capability(MNA_FEASIBILITY_REVIEW, frozenset({ADVISORY})),
    Capability(run_mna_review, frozenset({ADVISORY})),
    Capability(ENTERPRISE_KNOWLEDGE, frozenset({ADVISORY})),
    Capability(query_enterprise_kg, frozenset({ADVISORY})),
    Capability(record_advisory_opinion, frozenset({ADVISORY})),
    Capability(add_advisory_details, frozenset({ADVISORY})),
    Capability(lookup_advisory_reviews, frozenset({ADVISORY})),
    # look-up-only handoffs: the advisory agent consults the meeting / news agents itself (they delegate to no one)
    Capability(ask_meeting_agent, frozenset({ADVISORY})),
    Capability(ask_news_agent, frozenset({ADVISORY})),
)

REGISTRY = Registry(CAPABILITIES)

get_capability = REGISTRY.get
capabilities_for = REGISTRY.capabilities_for
skills_for = REGISTRY.skills_for
tools_for = REGISTRY.tools_for
