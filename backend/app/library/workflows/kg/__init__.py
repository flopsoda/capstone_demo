"""Enterprise Knowledge Graph — one agent system's meetings and news as entities, records and facts.

    store.py    KGStore(meeting_store, news_store, aliases=…): tables in the system's own schema, sync, query
    project.py  sources → raw layer: meetings (structural, no model) · news events (structural + LLM extraction)
    extract.py  the news relation extraction prompt and its answer, checked field by field
    resolve.py  raw layer → resolved layer: entity resolution, predicate normalization, fact identity, status
                (rule based, versioned; the swap point for better algorithms)
    query.py    what the Advisory Agent's query_enterprise_kg Tool reads, filtered by who asks
    build.py    CLI: first build / catch-up sync / re-resolve

Not agent-visible; no LangChain dependency (the model is passed in as ask(system, user) -> text).
"""
