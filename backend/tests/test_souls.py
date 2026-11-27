"""SOUL files: every agent reads who it is from app/systems/<system>/souls/<module>.md (no LLM needed)."""

import importlib
from pathlib import Path

import pytest

from app.runtime.souls import SoulMissing, load_soul, soul_path

SYSTEMS = Path(__file__).resolve().parent.parent / "app" / "systems"
AGENT_MODULES = sorted(p for p in SYSTEMS.glob("*/agents/*.py") if p.name != "__init__.py")


def test_every_agent_reads_its_soul_file():
    assert len(AGENT_MODULES) == 8
    for path in AGENT_MODULES:
        agent = importlib.import_module(f"app.systems.{path.parent.parent.name}.agents.{path.stem}")
        assert agent.IDENTITY == soul_path(path).read_text(encoding="utf-8").strip()
        assert agent.SYSTEM_PROMPT.startswith(agent.IDENTITY)  # identity first, Skills after it
        assert 'IDENTITY = """' not in path.read_text(encoding="utf-8")  # no second copy left in code


def test_every_soul_file_belongs_to_an_agent():
    souls = {(p.parent.parent.name, p.stem) for p in SYSTEMS.glob("*/souls/*.md")}
    assert souls == {(p.parent.parent.name, p.stem) for p in AGENT_MODULES}


def test_missing_or_empty_soul_stops_the_agent_module(tmp_path):
    module = tmp_path / "agents" / "scout.py"
    with pytest.raises(SoulMissing, match="not found"):
        load_soul(module)
    (tmp_path / "souls").mkdir()
    (tmp_path / "souls" / "scout.md").write_text("  \n", encoding="utf-8")
    with pytest.raises(SoulMissing, match="empty"):
        load_soul(module)
    (tmp_path / "souls" / "scout.md").write_text("\n당신은 정찰 에이전트다.\n", encoding="utf-8")
    assert load_soul(module) == "당신은 정찰 에이전트다."
