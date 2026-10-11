"""K Super graph control flow without an LLM: the run keeps looping back to the
model after each delegation until every registered specialist has reported."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.k_super import TOOLS_BY_NAME, _after_tools, pending_delegations


def _delegated(name: str, idx: int) -> list:
    return [
        AIMessage(content="", tool_calls=[{"name": name, "args": {"task": "t"}, "id": f"c{idx}"}]),
        ToolMessage(content="ok", tool_call_id=f"c{idx}", name=name),
    ]


def test_registry_gives_k_super_one_delegation_tool_per_specialist():
    assert list(TOOLS_BY_NAME) == ["delegate_to_meeting_agent", "delegate_to_news_agent", "delegate_to_advisory_agent"]


def test_loops_to_model_until_every_specialist_reported():
    messages = [HumanMessage("req")]
    assert pending_delegations(messages) == ["delegate_to_meeting_agent", "delegate_to_news_agent", "delegate_to_advisory_agent"]

    messages += _delegated("delegate_to_meeting_agent", 1)
    assert pending_delegations(messages) == ["delegate_to_news_agent", "delegate_to_advisory_agent"]
    assert _after_tools({"messages": messages}) == "model"

    messages += _delegated("delegate_to_news_agent", 2)
    assert pending_delegations(messages) == ["delegate_to_advisory_agent"]
    assert _after_tools({"messages": messages}) == "model"

    messages += _delegated("delegate_to_advisory_agent", 3)
    assert pending_delegations(messages) == []
    assert _after_tools({"messages": messages}) == "__end__"


def test_repeat_delegation_does_not_count_as_progress():
    messages = [HumanMessage("req"), *_delegated("delegate_to_meeting_agent", 1), *_delegated("delegate_to_meeting_agent", 2)]
    assert pending_delegations(messages) == ["delegate_to_news_agent", "delegate_to_advisory_agent"]
    assert _after_tools({"messages": messages}) == "model"
