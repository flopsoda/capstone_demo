"""Common agent-platform runtime, shared by every agent system in this repo.

    agent_core.py   Specialist / Super agent cores (LangGraph message-state loops, invocation contexts)
    capabilities.py Skill / Capability types and the Registry lookup mechanism
    events.py       lifecycle events: emit(), RunContext stamping, stream_run()
    llm.py          chat model factory
    schemas.py      API request models
    api.py          NDJSON streaming helper for run endpoints

Nothing here knows an agent id, a Capability, or a data file: that is the
agent systems' business (app/systems/*). Shared Capability *implementations*
live in app/library and are bound by each system to its own data.
"""
