-- 002: transcript 연결 · Compliance 검토 · 메일 발송함 · 다른 system의 업무 요청.
-- meeting_store.migrate()가 한 번만 적용한다 (schema_migrations). 이 파일은 고치지 말고 다음 번호로 추가한다.

-- ---------------------------------------------------------------------------
-- transcript: speech-service가 준 id로 가져온다 (같은 녹음을 두 번 붙이지 않도록)
-- ---------------------------------------------------------------------------
ALTER TABLE transcripts ADD COLUMN source_ref text UNIQUE;   -- speech-service transcript id (TR-20261028-1A2B3C)

-- ---------------------------------------------------------------------------
-- Compliance: 규칙에 걸린 발언 · 문장. 열려 있는(open) 항목이 있으면 회의록을 확정하지 않는다.
--   cleared       문제없음으로 판단 (오탐)
--   acknowledged  문제를 확인하고 조치함 (삭제 · 법무 검토 의뢰 등, review_note에 기록)
-- ---------------------------------------------------------------------------
CREATE TABLE compliance_flags (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    meeting_id     bigint NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    source         text NOT NULL CHECK (source IN ('minutes', 'transcript')),
    location       text NOT NULL,              -- "요약", "본문", "결정 D2", "transcript [03:15] 박지훈 (CFO)"
    transcript_id  bigint,                     -- source = transcript: 그 발언 (transcript가 만료되면 NULL)
    seq            int,
    category       text NOT NULL,              -- insider_trading, collusion, ...
    rule           text NOT NULL,              -- 규칙 이름 (근거 법령 포함)
    excerpt        text NOT NULL,              -- 걸린 부분 앞뒤 문맥
    status         text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'cleared', 'acknowledged')),
    review_note    text,
    reviewed_by    text,
    reviewed_at    timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (meeting_id, location, category),
    FOREIGN KEY (transcript_id, seq) REFERENCES transcript_segments (transcript_id, seq) ON DELETE SET NULL,
    CHECK ((status = 'open') = (reviewed_at IS NULL))
);
CREATE INDEX compliance_flags_open ON compliance_flags (meeting_id) WHERE status = 'open';

-- ---------------------------------------------------------------------------
-- 메일 발송함: Agent가 만든 메일은 승인(approved)된 뒤에만 발송 작업이 보낸다.
--   needs_address  받는 사람의 주소를 디렉터리에서 찾지 못함 → 주소를 받으면 pending
--   pending        승인 대기 (의장 · 간사 · 확정 권한 그룹)
--   approved       발송 대기 → sent | failed (재시도 후)
--   rejected       보내지 않음 (반려, 또는 새 알림으로 대체)
-- ---------------------------------------------------------------------------
CREATE TABLE outbox (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind            text NOT NULL CHECK (kind IN ('minutes', 'assignment', 'reminder', 'schedule')),
    meeting_id      bigint NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    action_item_id  bigint REFERENCES action_items(id) ON DELETE CASCADE,
    person_id       bigint REFERENCES people(id),
    to_name         text NOT NULL,
    to_email        text,
    subject         text NOT NULL,
    body            text NOT NULL,
    status          text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('needs_address', 'pending', 'approved', 'sent', 'failed', 'rejected')),
    dedupe_key      text UNIQUE,               -- 같은 알림을 두 번 만들지 않는다 (reminder:<item>:<due>:<D-3>)
    created_by      text,                      -- 요청자 email, 자동 알림은 'scheduler'
    created_at      timestamptz NOT NULL DEFAULT now(),
    approved_by     text,
    approved_at     timestamptz,
    sent_at         timestamptz,
    attempts        smallint NOT NULL DEFAULT 0,
    last_error      text,
    CHECK (status = 'needs_address' OR to_email IS NOT NULL),
    CHECK ((status = 'sent') = (sent_at IS NOT NULL))
);
CREATE INDEX outbox_status ON outbox (status, id);
CREATE INDEX outbox_meeting ON outbox (meeting_id);

-- ---------------------------------------------------------------------------
-- 다른 Agent System이 A2A로 보낸 업무 요청. 이 system의 확정 권한자가 접수(accepted)해야
-- 정식 Action Item이 된다 (다른 회사가 보낸 업무가 바로 기록되지 않도록).
-- ---------------------------------------------------------------------------
CREATE TABLE task_requests (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code             text NOT NULL UNIQUE,     -- REQ-2026-10-28-01
    from_system      text NOT NULL,            -- 요청한 system (A2A caller), 예: group
    requested_by     text,                     -- 그 system의 사용자 email (principal)
    requester_name   text,
    source_ref       text,                     -- 요청한 쪽의 기록 id (예: 그룹의 MTG-2026-10-20-01-A2)
    title            text NOT NULL,
    detail           text,
    suggested_owner  text,
    due_date         date,
    classification   smallint NOT NULL DEFAULT 2 CHECK (classification BETWEEN 1 AND 4),
    status           text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected')),
    decided_by       text,
    decided_at       timestamptz,
    decision_note    text,
    action_item_id   bigint REFERENCES action_items(id) ON DELETE SET NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CHECK ((status = 'pending') = (decided_at IS NULL))
);
CREATE INDEX task_requests_status ON task_requests (status, created_at);
