"""SOUL files: every agent reads who it is from app/systems/<system>/souls/<module>.md (no LLM needed)."""

import ast
import importlib
from pathlib import Path

import pytest

from app.runtime.agent_core import compose_system_prompt
from app.runtime.souls import SoulMissing, load_soul, soul_path

SYSTEMS = Path(__file__).resolve().parent.parent / "app" / "systems"
AGENT_MODULES = sorted(p for p in SYSTEMS.glob("*/agents/*.py") if p.name != "__init__.py")


def _module_assignments(path: Path, name: str) -> list[ast.expr]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n.value for n in tree.body
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]


def test_agents_exist_in_both_systems():
    assert {p.parent.parent.name for p in AGENT_MODULES} == {"group", "k_electronics"}


@pytest.mark.parametrize("path", AGENT_MODULES, ids=lambda p: f"{p.parent.parent.name}/{p.stem}")
def test_identity_comes_only_from_the_soul_file(path):
    # In code: exactly one `IDENTITY = load_soul(__file__)`, nothing else assigns it.
    [value] = _module_assignments(path, "IDENTITY")
    assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "load_soul"
    assert [a.id for a in value.args if isinstance(a, ast.Name)] == ["__file__"] and not value.keywords
    # No copy of the identity text left anywhere in the module, whatever the quoting.
    soul = load_soul(path)
    assert soul.splitlines()[0] not in path.read_text(encoding="utf-8")
    # At run time the system prompt is exactly the SOUL file followed by the agent's Registry Skills.
    system = path.parent.parent.name
    agent = importlib.import_module(f"app.systems.{system}.agents.{path.stem}")
    registry = importlib.import_module(f"app.systems.{system}.registry")
    assert agent.IDENTITY == soul
    assert agent.SYSTEM_PROMPT == compose_system_prompt(soul, registry.skills_for(agent.AGENT_ID))


def test_every_soul_file_belongs_to_an_agent():
    souls = {(p.parent.parent.name, p.name) for p in SYSTEMS.glob("*/souls/*")}
    assert souls == {(p.parent.parent.name, f"{p.stem}.md") for p in AGENT_MODULES}


def test_windows_edited_soul_gives_the_same_text(tmp_path):
    # A Windows editor may save with a BOM and CRLF; the prompt must not change (no '﻿', no '\r').
    module = tmp_path / "agents" / "scout.py"
    (tmp_path / "souls").mkdir()
    (tmp_path / "souls" / "scout.md").write_bytes("﻿당신은 정찰 에이전트다.\r\n역할: 살핀다.\r\n\r\n".encode("utf-8"))
    assert load_soul(module) == "당신은 정찰 에이전트다.\n역할: 살핀다."


def test_missing_empty_or_non_utf8_soul_stops_the_agent_module(tmp_path):
    module = tmp_path / "agents" / "scout.py"
    with pytest.raises(SoulMissing, match="not found"):
        load_soul(module)
    (tmp_path / "souls").mkdir()
    soul = tmp_path / "souls" / "scout.md"
    soul.write_text("  \n", encoding="utf-8")
    with pytest.raises(SoulMissing, match="empty"):
        load_soul(module)
    soul.write_bytes("당신은 정찰 에이전트다.".encode("cp949"))
    with pytest.raises(SoulMissing, match="not UTF-8"):
        load_soul(module)
    soul.write_text("\n당신은 정찰 에이전트다.\n", encoding="utf-8")
    assert load_soul(module) == "당신은 정찰 에이전트다."
