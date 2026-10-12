"""Shared Capability library: Skill texts, workflow functions and Tool factories
that any agent system may register in its own Registry.

    skills/     Skill definitions (playbooks). System-neutral wording.
    workflows/  Tool-internal workflows: pure functions over a data store given by `path`.
    tools/      Agent-visible Tool surfaces (name, description, argument schema) as
                factories: make_<tool>(path) returns the Tool bound to one system's data.

The library never imports an agent system. A system binds a Tool to its own
data (app/systems/<system>/tools/) and lists it, with permissions, in its own
Registry.
"""
