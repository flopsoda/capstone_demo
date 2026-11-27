"""SOUL files: who each agent is, kept as Markdown instead of a string in code.

An agent system keeps one file per agent: app/systems/<system>/agents/<name>.py reads
app/systems/<system>/souls/<name>.md. The file says who the agent is, what it does and does not
do, and its output rules. compose_system_prompt() puts the file first in the agent's system prompt
and adds the agent's Skills after it, so every line of the file reaches the model.

What goes where:
  SOUL file — who the agent is (identity, role, output rules); the same for every request.
  Skill     — how to carry out one kind of work (app/library/skills, app/systems/<system>/skills).
  Tool      — the data and actions the agent reaches at run time (meetings, news, the graph).
Permissions are enforced in code (gateway, principal, Tools), never by the SOUL text.
"""

from __future__ import annotations

from pathlib import Path


class SoulMissing(RuntimeError):
    """An agent module has no SOUL file, or the file is empty."""


def soul_path(agent_module: str | Path) -> Path:
    """app/systems/<system>/agents/<name>.py → app/systems/<system>/souls/<name>.md"""
    module = Path(agent_module)
    return module.parent.parent / "souls" / f"{module.stem}.md"


def load_soul(agent_module: str | Path) -> str:
    """The identity text of the agent defined in `agent_module` (pass __file__).

    Read once when the agent module is imported, so a missing or empty file stops the app
    at startup instead of leaving an agent without its identity mid-run."""
    path = soul_path(agent_module)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as e:
        raise SoulMissing(f"SOUL file not found: {path}") from e
    if not text:
        raise SoulMissing(f"SOUL file is empty: {path}")
    return text
