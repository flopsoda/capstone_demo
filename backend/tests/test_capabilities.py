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
        "inbound_task_requests": ("skill", {"meeting"}),
        "run_meeting_context_retrieval": ("tool", {"meeting"}),
        "save_meeting_minutes": ("tool", {"meeting"}),
        "run_action_item_tracking": ("tool", {"meeting"}),
        "update_action_item": ("tool", {"meeting"}),
        "run_meeting_schedule": ("tool", {"meeting"}),
        "manage_mail_outbox": ("tool", {"meeting"}),
        "run_task_requests": ("tool", {"meeting"}),
        "external_risk_analysis": ("skill", {"news"}),
        "news_briefing": ("skill", {"news"}),
        "run_news_briefing": ("tool", {"news"}),
        "run_company_risk_news_analysis": ("tool", {"news"}),
        "mna_feasibility_review": ("skill", {"advisory"}),
        "run_mna_review": ("tool", {"advisory"}),
        "enterprise_knowledge": ("skill", {"advisory"}),
        "query_enterprise_kg": ("tool", {"advisory"}),
        "record_advisory_opinion": ("tool", {"advisory"}),
        "add_advisory_details": ("tool", {"advisory"}),
        "lookup_advisory_reviews": ("tool", {"advisory"}),
        "ask_meeting_agent": ("tool", {"advisory"}),
        "ask_news_agent": ("tool", {"advisory"}),
    }


def test_tool_definition_is_the_source_of_truth():
    cap = get_capability("run_meeting_context_retrieval")
    assert isinstance(cap.impl, BaseTool)
    assert cap.description == cap.impl.description and "회의" in cap.description
    assert set(cap.impl.args) == {"query", "topics", "meeting_id", "date_from", "date_to"}
    for name, args in {
        "save_meeting_minutes": {"title", "date", "summary", "decisions", "action_items", "attendees", "topics", "body",
                                 "classification", "location", "meeting_id", "finalize", "correction_reason", "transcript_id",
                                 "flag_reviews", "start_time", "end_time", "checkin_code", "subject"},
        "run_action_item_tracking": {"query", "owner", "status", "due_before", "due_within_days", "overdue_only", "meeting_id", "mine"},
        "update_action_item": {"item_id", "status", "note", "due", "owner", "remind"},
        "run_meeting_schedule": {"action", "meeting_id", "title", "starts_at", "ends_at", "location", "attendees", "agenda",
                                 "classification", "carry_over_from", "date_from", "date_to", "mine"},
        "manage_mail_outbox": {"action", "mail_ids", "meeting_id", "email", "note"},
        "run_task_requests": {"action", "request_id", "title", "detail", "due", "suggested_owner", "source_ref", "classification",
                              "owner", "meeting_id", "note", "status"},
    }.items():
        impl = get_capability(name).impl
        assert isinstance(impl, BaseTool) and set(impl.args) == args and get_capability(name).description == impl.description
    # Who the call acts for is never a model argument.
    # (manage_mail_outbox's `email` is a recipient address the user gives, not the requester's identity.)
    assert not any({"principal", "viewer", "email", "clearance", "caller"} & set(get_capability(n).impl.args) for n in (
        "run_meeting_context_retrieval", "save_meeting_minutes", "run_action_item_tracking", "update_action_item", "run_meeting_schedule",
        "run_task_requests"))
    assert not {"principal", "viewer", "clearance", "caller"} & set(get_capability("manage_mail_outbox").impl.args)

    cap = get_capability("run_company_risk_news_analysis")
    assert isinstance(cap.impl, BaseTool)
    assert cap.description == cap.impl.description and "리스크" in cap.description
    assert set(cap.impl.args) == {"query", "company", "topics"}

    cap = get_capability("run_mna_review")
    assert isinstance(cap.impl, BaseTool)
    assert cap.description == cap.impl.description and "권고는 돌려주지 않는다" in cap.description
    assert set(cap.impl.args) == {"query", "company", "price_trillion_krw", "scenarios", "criteria"}
    out = cap.impl.invoke({"query": "인수 검토", "company": "뉴로엣지"})
    assert out["found"] and "recommendation" not in out and out["price"]["label"] == "최초 요구가"


def test_news_tool_reads_this_systems_news_store(news_db):
    assert get_capability("run_company_risk_news_analysis").impl.invoke({"query": "외부 리스크", "company": "뉴로엣지"})["matched"] == 3


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
        "meeting_context_analysis", "meeting_minutes_writing", "meeting_follow_up", "inbound_task_requests",
        "run_meeting_context_retrieval", "save_meeting_minutes", "run_action_item_tracking", "update_action_item", "run_meeting_schedule",
        "manage_mail_outbox", "run_task_requests",
    ]
    assert [c.name for c in capabilities_for("news")] == [
        "external_risk_analysis", "news_briefing", "run_news_briefing", "run_company_risk_news_analysis",
    ]
    assert [c.name for c in capabilities_for("advisory")] == [
        "mna_feasibility_review", "run_mna_review", "enterprise_knowledge", "query_enterprise_kg",
        "record_advisory_opinion", "add_advisory_details", "lookup_advisory_reviews", "ask_meeting_agent", "ask_news_agent",
    ]
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
    from app.systems.k_electronics.tools import ask_meeting_agent as am, ask_news_agent as an

    # the advisory agent consults the meeting / news agents itself, look-up only
    assert (am.FROM_AGENT, am.TO_AGENT) == ("advisory", "meeting") and am.ask_meeting_agent.metadata["read_only"] is True
    assert (an.FROM_AGENT, an.TO_AGENT) == ("advisory", "news") and an.ask_news_agent.metadata["read_only"] is True


def test_delegation_graph_has_no_cycle():
    """Who may hand work to whom, from the registries: the meeting and news agents delegate to no one, so a chain
    is at most Super → advisory → meeting / news and can never come back."""
    from app.systems.group import registry as group_registry

    for registry in (CAPABILITIES, group_registry.CAPABILITIES):
        edges = {}
        for c in registry:
            meta = getattr(c.impl, "metadata", None) or {}
            if c.kind == "tool" and meta.get("handoff"):
                for frm in c.allowed_agents:
                    edges.setdefault(frm, set()).add(meta["to"])
        assert edges.get("meeting") is None and edges.get("news") is None
        assert edges["advisory"] == {"meeting", "news"}

        def reach(a, seen=()):
            for b in edges.get(a, ()):
                assert b not in seen and b != a, (a, b)
                reach(b, (*seen, a))

        for agent in edges:
            reach(agent)
