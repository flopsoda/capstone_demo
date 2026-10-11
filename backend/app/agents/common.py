"""Shared pieces for the message-history based agents."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool

from ..events import emit

# Upper bound on LangGraph super-steps per agent invocation (model ↔ tools loop).
RECURSION_LIMIT = 10


def execute_tool_calls(agent: str, message: AIMessage, tools: dict[str, BaseTool]) -> list[ToolMessage]:
    """Runs every tool call the model made, emitting tool_start / tool_end around each."""
    out: list[ToolMessage] = []
    for call in message.tool_calls:
        emit({"type": "tool_start", "agent": agent, "tool": call["name"], "args": dict(call["args"])})
        tool = tools.get(call["name"])
        if tool is None:
            content: Any = {"error": f"unknown tool: {call['name']}"}
        else:
            content = tool.invoke(call["args"])
        emit({"type": "tool_end", "agent": agent, "tool": call["name"], "result": content})
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


def tool_call_names(messages: list[BaseMessage]) -> list[str]:
    return [call["name"] for m in messages if isinstance(m, AIMessage) for call in m.tool_calls]


def final_response(messages: list[BaseMessage]) -> str:
    """Text of the last AI message that is not a tool call."""
    for m in reversed(messages):
        if isinstance(m, AIMessage) and not m.tool_calls:
            return message_text(m)
    return ""
