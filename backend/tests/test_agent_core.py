"""Tool calls whose arguments do not fit the Tool's schema come back to the model as an error it can fix."""

import json

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from app.runtime.agent_core import execute_tool_calls
from app.runtime.events import new_run, stream_run


class Decision(BaseModel):
    statement: str


@tool
def save(decisions: list[Decision]) -> dict:
    """Save decisions."""
    return {"saved": len(decisions)}


def call(args):
    return AIMessage(content="", tool_calls=[{"name": "save", "args": args, "id": "c1"}])


def test_wrong_field_names_come_back_as_an_error_instead_of_ending_the_run():
    out = []
    run = new_run(system="group", root_agent="meeting")
    events = list(stream_run(lambda: out.extend(execute_tool_calls("meeting", call({"decisions": [{"criterion": "x"}]}), {"save": save})),
                             run=run))
    assert "decisions.0.statement: Field required" in out[0].content and "다시 호출" in out[0].content  # gemma4가 한 일
    assert [e["type"] for e in events] == ["tool_start", "tool_end", "done"]  # error 이벤트 없이 끝남
    assert json.loads(execute_tool_calls("meeting", call({"decisions": [{"statement": "x"}]}), {"save": save})[0].content) == {"saved": 1}
