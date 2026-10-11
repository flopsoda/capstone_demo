"""Shared pieces for the message-history based agents."""

from __future__ import annotations

import json
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool

# Upper bound on LangGraph super-steps per agent invocation (model ↔ tools loop).
RECURSION_LIMIT = 10


class ToolCallRecord(TypedDict):
    name: str
    args: dict[str, Any]
    result: Any


class AgentRun(TypedDict):
    """What an agent did for one request — the trace handed back to the caller."""

    agent: str
    request: str
    tool_calls: list[ToolCallRecord]
    response: str


def execute_tool_calls(message: AIMessage, tools: dict[str, BaseTool]) -> list[ToolMessage]:
    """Runs every tool call the model made and wraps results as ToolMessages."""
    out: list[ToolMessage] = []
    for call in message.tool_calls:
        tool = tools.get(call["name"])
        if tool is None:
            content: Any = {"error": f"unknown tool: {call['name']}"}
        else:
            content = tool.invoke(call["args"])
        out.append(
            ToolMessage(
                content=content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
                tool_call_id=call["id"],
                name=call["name"],
            )
        )
    return out


def message_text(message: BaseMessage) -> str:
    """Plain text of a message (models may return a list of content blocks)."""
    content = message.content
    if isinstance(content, str):
        return content.strip()
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(p for p in parts if p).strip()


def collect_tool_calls(messages: list[BaseMessage]) -> list[ToolCallRecord]:
    """Pairs each AI tool call with its ToolMessage result, in call order."""
    results: dict[str, Any] = {}
    for m in messages:
        if isinstance(m, ToolMessage):
            raw = m.content
            try:
                results[m.tool_call_id] = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                results[m.tool_call_id] = raw
    records: list[ToolCallRecord] = []
    for m in messages:
        if isinstance(m, AIMessage):
            for call in m.tool_calls:
                records.append(
                    {"name": call["name"], "args": dict(call["args"]), "result": results.get(call["id"])}
                )
    return records


def final_response(messages: list[BaseMessage]) -> str:
    """Text of the last AI message that is not a tool call."""
    for m in reversed(messages):
        if isinstance(m, AIMessage) and not m.tool_calls:
            return message_text(m)
    return ""
