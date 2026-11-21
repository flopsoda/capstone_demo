-- Enterprise KG of one agent system, in that system's own schema (kelec / grp) next to its meeting records,
-- so a fact from a meeting is checked against the meeting it came from (VISIBLE) and goes when the meeting goes.
-- Applied by kg.store.KGStore.migrate() (kg_schema_version). Built from this system's meeting records and its
-- news schema (kelec_news / grp_news) only — never another system's.
--
-- Two layers:
--   raw       what each source said, as it said it (surface, context, source, time, extractor version).
--             Re-made only when its source changes (kg_sources); an LLM answer is cached (kg_extractions).
--   resolved  entities, links and facts made from the raw layer by kg.resolve (rules, versioned). Rebuilt as a
--             whole from the raw layer, so a better resolver re-resolves everything without calling a model again.

-- ---------------------------------------------------------------------------
-- Raw layer
-- ---------------------------------------------------------------------------

CREATE TABLE kg_mentions (                      -- a name a source uses for something (company, person, product …)
    id           text PRIMARY KEY,              -- hash of (source, record, extractor, surface)
    source_kind  text NOT NULL,                 -- meeting | news_event
    source_ref   text NOT NULL,                 -- MTG-2026-09-15 | news event uuid
    record       text NOT NULL,                 -- the record node it was found in (decision:…, article:…, event:…)
    surface      text NOT NULL,                 -- the name as written
    aliases      text[] NOT NULL DEFAULT '{}',  -- other names the source gives it (뉴로엣지 ↔ NeuroEdge)
    type_guess   text,                          -- organization | person | product | other
    context      text NOT NULL DEFAULT '',      -- the sentence it was found in
    extractor    text NOT NULL,                 -- dict-v1 | analysis | news-rel-v1 …
    meeting_id   bigint REFERENCES meetings(id) ON DELETE CASCADE,   -- NULL: news (public)
    extracted_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX kg_mentions_source ON kg_mentions (source_kind, source_ref);

CREATE TABLE kg_claims (                        -- one statement of one source: subject —predicate→ object
    id            text PRIMARY KEY,             -- hash of (source, extractor, subject, predicate, object, local key)
    source_kind   text NOT NULL,
    source_ref    text NOT NULL,
    claim_class   text NOT NULL CHECK (claim_class IN ('structural', 'reported', 'assessed')),
                  -- structural: a record says so (meeting DB, article ↔ event)
                  -- reported:   a news text says so (LLM extraction)
                  -- assessed:   someone judged it (decision watch: a news item shook a decision's premise)
    subject       text NOT NULL,                -- a node id, or 'mention:<id>' for the resolver to link
    predicate_raw text NOT NULL,                -- the predicate as the source / extractor gave it
    object        text NOT NULL,
    qualifiers    jsonb NOT NULL DEFAULT '{}',  -- status, expression, article_ids, effect, judged, due, role …
    event_ref     text,                         -- the news event / decision the statement belongs to
    stated_at     date,                         -- when the source says it (article date, meeting date)
    context       text NOT NULL DEFAULT '',     -- the source's own words
    extractor     text NOT NULL,
    meeting_id    bigint REFERENCES meetings(id) ON DELETE CASCADE,   -- NULL: news (public)
    extracted_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX kg_claims_source ON kg_claims (source_kind, source_ref);

CREATE TABLE kg_sources (                       -- what each source looked like when it was last turned into claims
    source_kind  text NOT NULL,
    source_ref   text NOT NULL,
    source_hash  text NOT NULL,
    synced_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_kind, source_ref)
);

CREATE TABLE kg_extractions (                   -- an LLM extraction, kept so the same input is never sent again
    event_id     text NOT NULL,
    extractor    text NOT NULL,
    input_hash   text NOT NULL,
    output       jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, extractor, input_hash)
);

-- ---------------------------------------------------------------------------
-- Nodes: records (meetings, decisions, articles … — made by the projections) and entities (made by the resolver)
-- ---------------------------------------------------------------------------

CREATE TABLE kg_nodes (
    id          text PRIMARY KEY,               -- decision:MTG-2026-09-15-D1 · article:art_… · org:뉴로엣지 · person:12
    kind        text NOT NULL CHECK (kind IN ('entity', 'record')),
    type        text NOT NULL,                  -- meeting · decision · premise · action_item · news_event · article ·
                                                -- organization · person · org_unit · product · other
    label       text NOT NULL,
    attrs       jsonb NOT NULL DEFAULT '{}',
    meeting_id  bigint REFERENCES meetings(id) ON DELETE CASCADE,   -- records of a meeting: seen only by who may see it
    resolver    text                            -- entities: the resolver version that made them
);

CREATE TABLE kg_aliases (                       -- names of an entity (resolver output)
    alias_norm  text NOT NULL,
    node_id     text NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
    alias       text NOT NULL,
    PRIMARY KEY (alias_norm, node_id)
);
CREATE INDEX kg_aliases_trgm ON kg_aliases USING gin (alias_norm gin_trgm_ops);

CREATE TABLE kg_mention_links (                 -- which entity a mention names (resolver output)
    mention_id  text PRIMARY KEY REFERENCES kg_mentions(id) ON DELETE CASCADE,
    node_id     text NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
    method      text NOT NULL,                  -- seed | alias | new
    resolver    text NOT NULL
);

-- ---------------------------------------------------------------------------
-- Facts: claims that say the same thing about the same instance, merged (resolver output)
-- ---------------------------------------------------------------------------

CREATE TABLE kg_facts (
    id            text PRIMARY KEY,             -- hash of (subject, predicate, object, instance_key)
    subject       text NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
    predicate     text NOT NULL,                -- canonical (kg.resolve.PREDICATES)
    object        text NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
    instance_key  text NOT NULL DEFAULT '',     -- which occurrence: the news event of a reported fact, so two
                                                -- lawsuits between the same companies stay two facts
    fact_class    text NOT NULL CHECK (fact_class IN ('structural', 'reported', 'assessed')),
    status        text NOT NULL CHECK (status IN ('active', 'ended', 'superseded', 'contradicted', 'dismissed')),
    valid_from    date,
    valid_to      date,
    first_seen    date,
    last_seen     date,
    attrs         jsonb NOT NULL DEFAULT '{}',  -- expressions, status timeline, effect …
    resolver      text NOT NULL,
    UNIQUE (subject, predicate, object, instance_key)
);
CREATE INDEX kg_facts_subject ON kg_facts (subject);
CREATE INDEX kg_facts_object ON kg_facts (object);

CREATE TABLE kg_fact_support (                  -- every claim behind a fact (its sources)
    fact_id   text NOT NULL REFERENCES kg_facts(id) ON DELETE CASCADE,
    claim_id  text NOT NULL REFERENCES kg_claims(id) ON DELETE CASCADE,
    PRIMARY KEY (fact_id, claim_id)
);
CREATE INDEX kg_fact_support_claim ON kg_fact_support (claim_id);
