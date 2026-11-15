-- News store, one schema per agent system (kelec_news, grp_news). Applied by store.migrate()
-- inside that schema (search_path); numbered files run once each, recorded in schema_version.
-- Stable identifiers for the Enterprise KG: news_articles.id (from the normalized URL),
-- news_events.id (UUID, never reassigned), news_outlets.domain.

CREATE TABLE news_outlets (
    domain      text PRIMARY KEY,                -- e.g. yna.co.kr
    name        text NOT NULL,                   -- 연합뉴스
    first_seen  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE news_feeds (
    url             text PRIMARY KEY,
    outlet_domain   text NOT NULL REFERENCES news_outlets(domain),
    kind            text NOT NULL CHECK (kind IN ('rss', 'sitemap')),
    last_polled_at  timestamptz,
    last_status     text,                        -- ok | http_<code> | blocked | robots_disallowed | error …
    last_item_count integer
);

CREATE TABLE news_articles (
    id              text PRIMARY KEY,            -- 'art_' + sha256(normalized url)[:20]
    url             text NOT NULL UNIQUE,        -- normalized URL the article was discovered at
    canonical_url   text,                        -- the page's own canonical link, when it states one
    outlet_domain   text NOT NULL REFERENCES news_outlets(domain),
    title           text NOT NULL,
    summary         text NOT NULL DEFAULT '',    -- the feed's or the page's own description
    body            text NOT NULL DEFAULT '',    -- cleaned article text (full / partial only)
    content_level   text NOT NULL CHECK (content_level IN ('full', 'partial', 'snippet', 'title')),
    body_hash       text,                        -- whitespace-insensitive sha256 of body
    usage_notice    boolean NOT NULL DEFAULT false,  -- "AI 학습·활용 금지": body not sent to the LLM
    published_at    timestamptz,
    discovered_at   timestamptz NOT NULL DEFAULT now(),
    fetched_at      timestamptz,                 -- page fetch time (NULL: feed data only)
    fetch_status    text NOT NULL DEFAULT 'pending',  -- pending | ok | robots_disallowed | blocked | http_<code> | timeout | error
    feed_url        text,
    syndicated_from text REFERENCES news_articles(id)  -- a wire story run by another outlet
);
CREATE INDEX news_articles_published ON news_articles (published_at DESC);
CREATE INDEX news_articles_pending ON news_articles (fetch_status) WHERE fetch_status = 'pending';
CREATE INDEX news_articles_body_hash ON news_articles (body_hash) WHERE body_hash IS NOT NULL;
CREATE INDEX news_articles_title_trgm ON news_articles USING gin (title public.gin_trgm_ops);
CREATE INDEX news_articles_text_trgm ON news_articles USING gin ((summary || ' ' || body) public.gin_trgm_ops);

-- Surface forms an article names. entity_ref stays NULL here: resolving a surface to a KG
-- entity is the KG's job; this column is where its answer goes.
CREATE TABLE news_mentions (
    article_id  text NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
    surface     text NOT NULL,
    mentions    integer NOT NULL,
    in_title    boolean NOT NULL,
    entity_ref  text,
    PRIMARY KEY (article_id, surface)
);
CREATE INDEX news_mentions_surface ON news_mentions (surface);
CREATE INDEX news_mentions_entity ON news_mentions (entity_ref) WHERE entity_ref IS NOT NULL;

-- Reports of one matter. An article joins one event once and keeps it.
CREATE TABLE news_events (
    id              uuid PRIMARY KEY,
    title           text NOT NULL,               -- headline of its first article
    first_published timestamptz,
    last_published  timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE news_event_articles (
    article_id  text PRIMARY KEY REFERENCES news_articles(id) ON DELETE CASCADE,
    event_id    uuid NOT NULL REFERENCES news_events(id) ON DELETE CASCADE,
    assigned_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX news_event_articles_event ON news_event_articles (event_id);

-- Per article, toward one target (the normalized subject of a request): how it reports the target.
CREATE TABLE news_article_analysis (
    article_id       text NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
    target           text NOT NULL,
    analyzer_version text NOT NULL,
    stance           text NOT NULL CHECK (stance IN ('favorable', 'critical', 'neutral', 'unknown')),
    article_type     text NOT NULL,              -- report | opinion | press_release | wire_copy
    risk_category    text,
    risk_level       text CHECK (risk_level IN ('HIGH', 'MEDIUM', 'LOW')),
    key_facts        jsonb NOT NULL DEFAULT '[]',
    entities         jsonb NOT NULL DEFAULT '[]',  -- organizations / people the article is about
    basis            text NOT NULL,              -- body | lead | snippet | title: what the analysis read
    method           text NOT NULL,              -- llm | rule | fixture
    details          jsonb NOT NULL DEFAULT '{}',
    analyzed_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (article_id, target, analyzer_version)
);

-- Per event, toward one target: what happened and what it means for the target.
CREATE TABLE news_event_analysis (
    event_id         uuid NOT NULL REFERENCES news_events(id) ON DELETE CASCADE,
    target           text NOT NULL,
    analyzer_version text NOT NULL,
    summary          text NOT NULL,
    key_facts        jsonb NOT NULL DEFAULT '[]',  -- [{text, article_ids}]
    impact           text NOT NULL CHECK (impact IN ('positive', 'negative', 'mixed', 'neutral', 'unknown')),
    risk_category    text,
    risk_level       text CHECK (risk_level IN ('HIGH', 'MEDIUM', 'LOW')),
    importance_llm   text,
    implication      text NOT NULL DEFAULT '',
    method           text NOT NULL,
    details          jsonb NOT NULL DEFAULT '{}',
    analyzed_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, target, analyzer_version)
);

-- One pipeline run (a request's collection or a scheduled one).
CREATE TABLE news_ingest_runs (
    id          bigserial PRIMARY KEY,
    trigger     text NOT NULL CHECK (trigger IN ('request', 'scheduled')),
    query       text,
    started_at  timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    stats       jsonb NOT NULL DEFAULT '{}'
);
