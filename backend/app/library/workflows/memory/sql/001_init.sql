-- Conversation memory of one agent system's Super Agent, in that system's own schema (kelec / grp).
-- Applied by memory.store.MemoryStore.migrate() (mem_schema_version).
--
-- A turn is one direct exchange (the user's current request, the Super Agent's answer, what it handed off),
-- kept once per run. Its text is split into chunks; each chunk has one vector per embedding model, so a new model
-- is added next to the old one (memory.reembed) and vectors of different sizes are never compared.

CREATE TABLE mem_turns (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id      text NOT NULL UNIQUE,           -- the run that made it: the same run is never kept twice
    agent       text NOT NULL,                  -- group_super | k_super
    user_key    text NOT NULL,                  -- whose conversation: the principal's email, lower case
    user_text   text NOT NULL,                  -- the current request ([이전 대화] the gateway attaches is not kept again)
    response    text NOT NULL,
    handoffs    jsonb NOT NULL DEFAULT '[]',    -- [{tool, task | request}]: what the Super Agent handed to whom
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX mem_turns_user ON mem_turns (user_key, created_at DESC);

CREATE TABLE mem_chunks (
    id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    turn_id  bigint NOT NULL REFERENCES mem_turns(id) ON DELETE CASCADE,
    seq      smallint NOT NULL,
    text     text NOT NULL,
    UNIQUE (turn_id, seq)
);
CREATE INDEX mem_chunks_text_trgm ON mem_chunks USING gin (text gin_trgm_ops);

CREATE TABLE mem_embeddings (
    chunk_id     bigint NOT NULL REFERENCES mem_chunks(id) ON DELETE CASCADE,
    model        text NOT NULL,                 -- provider:model, e.g. openai:text-embedding-3-small
    embedding    public.vector NOT NULL,
    embedded_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chunk_id, model)
);
