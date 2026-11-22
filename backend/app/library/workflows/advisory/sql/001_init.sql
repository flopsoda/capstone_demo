-- The Advisory Agent's verified judgements, with a copy of the evidence each rests on (advisory/store.py).
CREATE TABLE IF NOT EXISTS adv_reviews (
    id                 bigserial PRIMARY KEY,
    code               text NOT NULL UNIQUE,              -- ADV-2026-11-22-01
    company            text NOT NULL,
    company_id         text,
    evaluation_type    text NOT NULL,                     -- initial | reevaluation | what_if
    recommendation     text NOT NULL,                     -- PROCEED | CONDITIONAL_PROCEED | DO_NOT_PROCEED (the model's)
    price_trillion_krw numeric,
    opinion            jsonb NOT NULL,                    -- the judgement as the model wrote it (checked by opinion.check)
    evidence           jsonb NOT NULL,                    -- the records it cites and the analyses it rests on
    prior_code         text REFERENCES adv_reviews (code),
    classification     smallint NOT NULL DEFAULT 1,       -- the highest classification of the meetings it draws on
    meeting_codes      text[] NOT NULL DEFAULT '{}',      -- those meetings: a reader must be able to see every one
    evidence_as_of     date,
    created_by         text,
    created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS adv_reviews_company ON adv_reviews (company, created_at DESC);
