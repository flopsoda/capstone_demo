"""Capability Registry of the Group agent system (content; mechanism in runtime.capabilities).

Group Super has two kinds of handoff Tools: delegation to this system's own
specialists and an A2A request to the K Electronics system. Nothing here
steers the model toward one or the other; it decides from the request and
the Tools' descriptions.
"""

from __future__ import annotations

from ...library.skills.decision_review import DECISION_REVIEW
from ...library.skills.enterprise_knowledge import ENTERPRISE_KNOWLEDGE
from ...library.skills.external_risk_analysis import EXTERNAL_RISK_ANALYSIS
from ...library.skills.news_briefing import NEWS_BRIEFING
from ...library.skills.meeting_context_analysis import MEETING_CONTEXT_ANALYSIS
from ...library.skills.meeting_follow_up import MEETING_FOLLOW_UP
from ...library.skills.meeting_minutes_writing import MEETING_MINUTES_WRITING
from ...library.skills.mna_feasibility_review import MNA_FEASIBILITY_REVIEW
from ...library.skills.specialist_delegation import SPECIALIST_DELEGATION
from ...runtime.capabilities import Capability, Registry
from .skills.subsidiary_review import SUBSIDIARY_REVIEW
from .tools.delegate_to_advisory_agent import delegate_to_advisory_agent
from .tools.delegate_to_meeting_agent import delegate_to_meeting_agent
from .tools.delegate_to_news_agent import delegate_to_news_agent
from .tools.request_k_electronics_review import request_k_electronics_review
from .tools.manage_mail_outbox import manage_mail_outbox
from .tools.run_company_risk_news_analysis import run_company_risk_news_analysis
from .tools.correct_transcript import correct_transcript
from .tools.run_decision_review import run_decision_review
from .tools.run_news_briefing import run_news_briefing
from .tools.run_action_item_tracking import run_action_item_tracking
from .tools.run_meeting_context_retrieval import run_meeting_context_retrieval
from .tools.run_meeting_schedule import run_meeting_schedule
from .tools.advisory_reviews import add_advisory_details, lookup_advisory_reviews, record_advisory_opinion
from .tools.ask_meeting_agent import ask_meeting_agent
from .tools.ask_news_agent import ask_news_agent
from .tools.query_enterprise_kg import query_enterprise_kg
from .tools.run_mna_review import run_mna_review
from .tools.save_meeting_minutes import save_meeting_minutes
from .tools.update_action_item import update_action_item

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
    Capability(MEETING_MINUTES_WRITING, frozenset({MEETING})),
    Capability(MEETING_FOLLOW_UP, frozenset({MEETING})),
    Capability(DECISION_REVIEW, frozenset({MEETING})),
    Capability(run_meeting_context_retrieval, frozenset({MEETING})),
    Capability(run_decision_review, frozenset({MEETING})),
    Capability(correct_transcript, frozenset({MEETING})),
    Capability(save_meeting_minutes, frozenset({MEETING})),
    Capability(run_action_item_tracking, frozenset({MEETING})),
    Capability(update_action_item, frozenset({MEETING})),
    Capability(run_meeting_schedule, frozenset({MEETING})),
    Capability(manage_mail_outbox, frozenset({MEETING})),
    # News Agent (Group's article store)
    Capability(EXTERNAL_RISK_ANALYSIS, frozenset({NEWS})),
    Capability(NEWS_BRIEFING, frozenset({NEWS})),
    Capability(run_news_briefing, frozenset({NEWS})),
    Capability(run_company_risk_news_analysis, frozenset({NEWS})),
    # Management Advisory Agent (Group's company store)
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
