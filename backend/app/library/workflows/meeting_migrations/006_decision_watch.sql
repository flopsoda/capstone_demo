-- 006: 결정의 전제 · 조건과, 회의가 끝난 뒤 그 전제를 흔드는 신호(뉴스 등).
-- meeting_store.migrate()가 한 번만 적용한다 (schema_migrations). 이 파일은 고치지 말고 다음 번호로 추가한다.

-- ---------------------------------------------------------------------------
-- 결정은 "어떤 전제에서" 내린 것인지와 함께 남는다. 회의가 끝난 뒤 전제가 깨지면 결정을 다시 봐야 한다.
--   kind         premise 전제 (예: HBM 단가가 내년 말까지 현재 수준 유지)
--                condition 조건 (예: 내년 3월까지 수율 85% 달성) — 지켜야 결정이 유효하다
--   review_when  회의에서 밝힌 재검토 기준 (예: 단가가 15% 넘게 떨어지면 투자 규모 재검토)
--   watch        뉴스 · 보고에서 이 전제와 관련된 것을 고를 낱말 (예: {HBM, 단가})
--   status       holding 유지 · at_risk 흔들림 · broken 깨짐 · met 조건 달성
-- ---------------------------------------------------------------------------
CREATE TABLE decision_premises (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    decision_id           bigint NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    seq                   smallint NOT NULL,
    kind                  text NOT NULL DEFAULT 'premise' CHECK (kind IN ('premise', 'condition')),
    statement             text NOT NULL,
    review_when           text,
    watch                 text[] NOT NULL DEFAULT '{}',
    due_date              date,                -- 조건의 기한 (예: 2027-03-31)
    speaker_id            bigint REFERENCES people(id),
    speaker_label         text,
    status                text NOT NULL DEFAULT 'holding' CHECK (status IN ('holding', 'at_risk', 'broken', 'met')),
    source_transcript_id  bigint,              -- 근거 발언 (transcript가 만료되면 NULL)
    source_seq            int,
    UNIQUE (decision_id, seq),
    FOREIGN KEY (source_transcript_id, source_seq)
        REFERENCES transcript_segments (transcript_id, seq) ON DELETE SET NULL
);

-- 전제 하나와 기사 하나를 대조한 결과 (한 번만 대조한다). effect가 none이면 화면에 보이지 않는다.
--   effect   broken 전제가 깨짐 · at_risk 흔들림 · none 관련 없음
--   judged   model 모델이 판단 · keywords 모델이 답하지 못해 낱말만 맞춘 것 (사람이 확인해야 함)
--   status   open 확인 전 · reviewed 재검토로 넘김 · dismissed 문제없음
CREATE TABLE premise_signals (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    premise_id   bigint NOT NULL REFERENCES decision_premises(id) ON DELETE CASCADE,
    source_kind  text NOT NULL DEFAULT 'news' CHECK (source_kind IN ('news', 'report', 'user')),
    source_ref   text NOT NULL,               -- NEWS-G-2026-11-03-01
    source_name  text,                        -- 테크데일리 (가상)
    source_date  date,
    headline     text NOT NULL,
    summary      text,
    effect       text NOT NULL CHECK (effect IN ('broken', 'at_risk', 'none')),
    reason       text,
    judged       text NOT NULL DEFAULT 'model' CHECK (judged IN ('model', 'keywords')),
    status       text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'reviewed', 'dismissed')),
    reviewed_by  text,
    review_note  text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (premise_id, source_ref)
);
CREATE INDEX premise_signals_open ON premise_signals (premise_id) WHERE status = 'open' AND effect <> 'none';

-- 전제가 흔들리면 결정에 관계된 사람에게 메일 (kind alert)
ALTER TABLE outbox DROP CONSTRAINT IF EXISTS outbox_kind_check;
ALTER TABLE outbox ADD CONSTRAINT outbox_kind_check CHECK (kind IN ('minutes', 'assignment', 'reminder', 'schedule', 'alert'));
