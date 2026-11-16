-- Semantic search: one vector per article and embedding model (pgvector, public.vector; the
-- dimension is the model's). content_level is the level of the text that was embedded: when the
-- article's text gets better (feed description → page body) it is embedded again.
-- Search compares every vector in the request's window (exact); past ~100k articles add an HNSW
-- index per model: ((embedding::vector(768)) vector_cosine_ops) WHERE model = '...'.
CREATE TABLE news_article_embeddings (
    article_id    text NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
    model         text NOT NULL,
    content_level text NOT NULL,
    embedding     public.vector NOT NULL,
    embedded_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (article_id, model)
);
