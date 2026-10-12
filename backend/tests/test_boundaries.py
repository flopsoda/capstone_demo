"""Import boundaries between the shared runtime, the shared Capability library
and the agent systems (docs/architecture.md §11):

    runtime  → imports neither library nor systems
    library  → imports runtime only (never a system)
    system X → never imports system Y
    app.main → the K Electronics entry shim; may import its system
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"


def _module_name(path: Path) -> str:
    rel = path.relative_to(APP.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(path: Path) -> set[str]:
    """Absolute module names imported anywhere in the file (relative imports resolved)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module = _module_name(path)
    package = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                target = ".".join([*base, node.module] if node.module else base)
            else:
                target = node.module or ""
            out.add(target)
            # `from pkg import name` may name a submodule; record it too.
            out.update(f"{target}.{alias.name}" for alias in node.names)
    return {m for m in out if m.startswith("app")}


def _area(module: str) -> tuple[str, str | None]:
    parts = module.split(".")
    if len(parts) >= 2 and parts[1] in ("runtime", "library"):
        return parts[1], None
    if len(parts) >= 3 and parts[1] == "systems":
        return "systems", parts[2]
    return "root", None


def test_runtime_library_and_systems_do_not_cross_their_boundaries():
    violations: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        module = _module_name(path)
        area, system = _area(module)
        for imported in _imports(path):
            iarea, isystem = _area(imported)
            bad = (
                (area == "runtime" and iarea in ("library", "systems"))
                or (area == "library" and iarea == "systems")
                or (area == "systems" and iarea == "systems" and isystem != system)
            )
            if bad:
                violations.append(f"{module} → {imported}")
    assert not violations, "\n".join(violations)


def test_every_system_has_its_own_registry_and_data():
    systems = [p for p in (APP / "systems").iterdir() if p.is_dir() and not p.name.startswith("_")]
    assert systems, "no agent systems found"
    for system in systems:
        assert (system / "registry.py").exists(), system.name
        assert (system / "data").is_dir(), system.name
