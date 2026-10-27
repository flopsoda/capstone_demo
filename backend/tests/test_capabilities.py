"""Capability Registry: what exists, skill vs tool, who may use it, and that the
agents really take their subset from it (no LLM needed)."""

from langchain_core.tools import BaseTool

from app.runtime.capabilities import Skill
from app.systems.k_electronics.registry import CAPABILITIES, capabilities_for, get_capability, skills_for, tools_for


def test_registry_lists_capabilities_with_permissions():
    table = {c.name: (c.kind, set(c.allowed_agents)) for c in CAPABILITIES}
    assert table == {
        "specialist_delegation": ("skill", {"k_super"}),
        "delegate_to_meeting_agent": ("tool", {"k_super"}),
        "delegate_to_news_agent": ("tool", {"k_super"}),
        "delegate_to_advisory_agent": ("tool", {"k_super"}),
        "meeting_context_analysis": ("skill", {"meeting"}),
        "meeting_minutes_writing": ("skill", {"meeting"}),
        "meeting_follow_up": ("skill", {"meeting"}),
        "run_meeting_context_retrieval": ("tool", {"meeting"}),
        "save_meeting_minutes": ("tool", {"meeting"}),
        "run_action_item_tracking": ("tool", {"meeting"}),
        "update_action_item": ("tool", {"meeting"}),
        "run_meeting_schedule": ("tool", {"meeting"}),
        "external_risk_analysis": ("skill", {"news"}),
        "run_company_risk_news_analysis": ("tool", {"news"}),
        "mna_feasibility_review": ("skill", {"advisory"}),
        "run_mna_review": ("tool", {"advisory"}),
    }


def test_tool_definition_is_the_source_of_truth():
    cap = get_capability("run_meeting_context_retrieval")
    assert isinstance(cap.impl, BaseTool)
    assert cap.description == cap.impl.description and "회의" in cap.description
    assert set(cap.impl.args) == {"query", "topics", "meeting_id", "date_from", "date_to"}
    for name, args in {
        "save_meeting_minutes": {"title", "date", "summary", "decisions", "action_items", "attendees", "topics", "body",
                                 "classification", "location", "meeting_id", "finalize", "correction_reason"},
        "run_action_item_tracking": {"query", "owner", "status", "due_before", "due_within_days", "overdue_only", "meeting_id", "mine"},
        "update_action_item": {"item_id", "status", "note", "due", "owner"},
        "run_meeting_schedule": {"action", "meeting_id", "title", "starts_at", "ends_at", "location", "attendees", "agenda",
                                 "classification", "carry_over_from", "date_from", "date_to", "mine"},
    }.items():
        impl = get_capability(name).impl
        assert isinstance(impl, BaseTool) and set(impl.args) == args and get_capability(name).description == impl.description
    # Who the call acts for is never a model argument.
    assert not any({"principal", "viewer", "email", "clearance"} & set(get_capability(n).impl.args) for n in (
        "run_meeting_context_retrieval", "save_meeting_minutes", "run_action_item_tracking", "update_action_item", "run_meeting_schedule"))

    cap = get_capability("run_company_risk_news_analysis")
    assert isinstance(cap.impl, BaseTool)
    assert cap.description == cap.impl.description and "리스크" in cap.description
    assert set(cap.impl.args) == {"query", "company", "topics"}
    assert cap.impl.invoke({"query": "외부 리스크", "company": "뉴로엣지"})["matched"] == 3

    cap = get_capability("run_mna_review")
    assert isinstance(cap.impl, BaseTool)
    assert cap.description == cap.impl.description and "persona" in cap.description
    assert set(cap.impl.args) == {"query", "company", "persona", "context"}
    out = cap.impl.invoke({"query": "인수 검토", "company": "뉴로엣지", "persona": "CFO"})
    assert out["recommendation"] == "CONDITIONAL_PROCEED" and out["persona"] == "CFO"


def test_meeting_tool_reads_this_systems_store(meeting_db):
    out = get_capability("run_meeting_context_retrieval").impl.invoke({"query": "뉴로엣지 인수", "topics": ["M&A"]})
    assert out["matched"] == 1


def test_each_agent_gets_only_its_subset():
    assert [c.name for c in capabilities_for("k_super")] == [
        "specialist_delegation",
        "delegate_to_meeting_agent",
        "delegate_to_news_agent",
        "delegate_to_advisory_agent",
    ]
    assert [c.name for c in capabilities_for("meeting")] == [
        "meeting_context_analysis", "meeting_minutes_writing", "meeting_follow_up",
        "run_meeting_context_retrieval", "save_meeting_minutes", "run_action_item_tracking", "update_action_item", "run_meeting_schedule",
    ]
    assert [c.name for c in capabilities_for("news")] == ["external_risk_analysis", "run_company_risk_news_analysis"]
    assert [c.name for c in capabilities_for("advisory")] == ["mna_feasibility_review", "run_mna_review"]
    assert capabilities_for("group_super") == []
    for agent in ("meeting", "news", "advisory"):
        assert all(isinstance(s, Skill) for s in skills_for(agent))
        assert all(isinstance(t, BaseTool) for t in tools_for(agent))


def test_agents_bind_registry_subset_and_skills_enter_system_prompt():
    from app.systems.k_electronics.agents import advisory, k_super, meeting, news

    for agent in (k_super, meeting, news, advisory):
        assert [t.name for t in agent.TOOLS] == [t.name for t in tools_for(agent.AGENT_ID)]
        assert agent.IDENTITY.strip() in agent.SYSTEM_PROMPT
        for skill in skills_for(agent.AGENT_ID):
            assert skill.name in agent.SYSTEM_PROMPT and skill.instructions.strip() in agent.SYSTEM_PROMPT
    # Agent ids used for permissions are the real agent ids.
    known = {k_super.AGENT_ID, meeting.AGENT_ID, news.AGENT_ID, advisory.AGENT_ID}
    assert all(c.allowed_agents <= known for c in CAPABILITIES)


def test_delegation_tools_point_at_registered_agents():
    from app.systems.k_electronics.tools import delegate_to_advisory_agent as da, delegate_to_meeting_agent as dm, delegate_to_news_agent as dn

    assert (dm.FROM_AGENT, dm.TO_AGENT) == ("k_super", "meeting")
    assert (dn.FROM_AGENT, dn.TO_AGENT) == ("k_super", "news")
    assert (da.FROM_AGENT, da.TO_AGENT) == ("k_super", "advisory")
