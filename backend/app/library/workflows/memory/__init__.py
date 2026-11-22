"""Conversation memory of a Super Agent: its direct conversations kept per user, embedded, and recalled by meaning.

    memory.py     SuperAgentMemory (runtime.memory.ConversationMemory): remember after a direct run, recall before one
    store.py      MemoryStore: mem_* tables in the system's own schema, search by meaning (pgvector) and by words (pg_trgm)
    embedding.py  embedding providers (OpenAI · Ollama), chosen by MEMORY_EMBED_*
    reembed.py    CLI: vectors of the configured model for chunks that lack one (after a model change)

Not agent-visible: the runtime calls it around a Super Agent's direct run.
"""
