-- Articles registered from a data file (store seed) rather than discovered in a feed: they may have
-- no original link. url stays UNIQUE for the articles that have one.
ALTER TABLE news_articles ALTER COLUMN url DROP NOT NULL;
