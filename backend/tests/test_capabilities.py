"""Capability Registry: what exists, skill vs tool, who may use it, and that the
agents really take their subset from it (no LLM needed)."""

from langchain_core.tools import BaseTool

from app.capabilities import CAPABILITIES, Skill, capabilities_for, get_capability, skills_for, tools_for


def test_registry_lists_step3_capabilities_with_permissions():
    table = {c.name: (c.kind, set(c.allowed_agents)) for c in CAPABILITIES}
    assert table == {
        "specialist_delegation": ("skill", {"k_super"}),
        "delegate_to_meeting_agent": ("tool", {"k_super"}),
        "meeting_context_analysis": ("skill", {"meeting"}),
        "run_meeting_context_retrieval": ("tool", {"meeting"}),
    }


def test_tool_definition_is_the_source_of_truth():
    cap = get_capability("run_meeting_context_retrieval")
    assert isinstance(cap.impl, BaseTool)
    assert cap.description == cap.impl.description and "회의" in cap.description
    assert set(cap.impl.args) == {"query", "topics"}
    assert cap.impl.invoke({"query": "뉴로엣지 인수", "topics": ["M&A"]})["matched"] == 1


def test_each_agent_gets_only_its_subset():
    assert [c.name for c in capabilities_for("k_super")] == ["specialist_delegation", "delegate_to_meeting_agent"]
    assert [c.name for c in capabilities_for("meeting")] == ["meeting_context_analysis", "run_meeting_context_retrieval"]
    assert capabilities_for("news") == []
    assert all(isinstance(s, Skill) for s in skills_for("meeting"))
    assert all(isinstance(t, BaseTool) for t in tools_for("meeting"))


def test_agents_bind_registry_subset_and_skills_enter_system_prompt():
    from app.agents import k_super, meeting

    assert [t.name for t in k_super.TOOLS] == [t.name for t in tools_for(k_super.AGENT_ID)]
    assert [t.name for t in meeting.TOOLS] == [t.name for t in tools_for(meeting.AGENT_ID)]
    for agent in (k_super, meeting):
        assert agent.IDENTITY.strip() in agent.SYSTEM_PROMPT
        for skill in skills_for(agent.AGENT_ID):
            assert skill.name in agent.SYSTEM_PROMPT and skill.instructions.strip() in agent.SYSTEM_PROMPT
    # Agent ids used for permissions are the real agent ids.
    known = {k_super.AGENT_ID, meeting.AGENT_ID}
    assert all(c.allowed_agents <= known for c in CAPABILITIES)
