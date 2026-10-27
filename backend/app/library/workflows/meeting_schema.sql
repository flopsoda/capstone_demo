-- Meeting store schema (applied by meeting_store.MeetingStore into one agent system's own schema).
-- Statements are unqualified: they land in the schema first on the search_path.
-- pg_trgm (gin_trgm_ops) must already exist in `public`; MeetingStore creates it when missing.
--
-- 기재 항목의 기준 — 공공기록물법 시행령 제18조②: 회의 명칭(meetings.title) · 개최기관(host_unit_id) ·
--   일시·장소 · 참석자·배석자(meeting_participants) · 진행 순서·상정 안건(agenda_items) · 발언 요지(minutes.body,
--   decisions opinion) · 결정 사항 및 표결 내용(decisions decision|resolution, votes_*, decision_dissents).
-- 의결기구(meeting_series.formal): 상법 제391조의3 — 반대하는 자와 그 반대이유, 출석자 서명(signed_at).
-- 접근 규칙(gateway shared/access.py): classification <= clearance AND (allowed_groups IS NULL OR 겹침).
--   하위 테이블은 소속 회의의 등급을 따른다. 자료(documents)만 자체 등급.
-- 보존: 회의록 · 결정 · Action Item은 장기 보존. transcript는 expires_at이 지나면 지운다
--   (지워지면 결정 · Action Item의 근거 발언 링크만 NULL이 된다).

-- ---------------------------------------------------------------------------
-- 1. 사람 · 조직
-- ---------------------------------------------------------------------------

CREATE TABLE org_units (                       -- 부서/팀: Action Item 담당이 사람이 아니라 팀인 경우가 많다
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       text NOT NULL UNIQUE,           -- 재무기획팀
    parent_id  bigint REFERENCES org_units(id) -- "본부 전체" 질의가 필요할 때만 (근거 약함)
);

CREATE TABLE people (                          -- 회의 참석자 디렉터리 (외부인 포함)
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    keycloak_sub  text UNIQUE,                 -- 내부 사용자; 외부인은 NULL
    display_name  text NOT NULL,               -- 박지훈
    title         text,                        -- CFO
    org_unit_id   bigint REFERENCES org_units(id),
    organization  text,                        -- 외부인 소속 (예: OO회계법인)
    email         text,
    is_external   boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 2. 회의체 · 일정
-- ---------------------------------------------------------------------------

CREATE TABLE meeting_series (                  -- 회의체 (정기 회의)
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                    text NOT NULL UNIQUE,    -- AI 반도체 전략회의, 그룹 투자심의위원회
    description             text,
    formal                  boolean NOT NULL DEFAULT false,  -- 의결기구: 확정 전에 표결 · 반대이유 · 출석자 서명 필요
    rrule                   text,                    -- RFC 5545 반복 규칙 (선택), 예: FREQ=MONTHLY;BYDAY=2TU
    default_classification  smallint NOT NULL DEFAULT 2 CHECK (default_classification BETWEEN 1 AND 4),
    default_allowed_groups  text[],
    secretary_id            bigint REFERENCES people(id),   -- 간사
    active                  boolean NOT NULL DEFAULT true
);

CREATE TABLE meetings (                        -- 회의 1회 = 일정 + 메타데이터
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code              text NOT NULL UNIQUE,    -- MTG-2026-10-25-01: Agent · 사용자가 부르는 id
    series_id         bigint REFERENCES meeting_series(id),
    title             text NOT NULL,
    host_unit_id      bigint REFERENCES org_units(id),   -- 주관 부서 (개최기관)
    status            text NOT NULL DEFAULT 'scheduled'
                      CHECK (status IN ('scheduled', 'held', 'cancelled', 'postponed')),
    starts_at         timestamptz NOT NULL,
    ends_at           timestamptz,
    location          text,                    -- 회의실 / 화상회의 링크
    topics            text[] NOT NULL DEFAULT '{}',
    classification    smallint NOT NULL DEFAULT 2 CHECK (classification BETWEEN 1 AND 4),
    allowed_groups    text[],                  -- NULL이면 등급만으로 판단
    rescheduled_from  bigint REFERENCES meetings(id),
    created_by        text,                    -- 요청자 email (gateway principal)
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_at IS NULL OR ends_at > starts_at)
);
CREATE INDEX meetings_starts_at ON meetings (starts_at);
CREATE INDEX meetings_series ON meetings (series_id, starts_at);
CREATE INDEX meetings_topics ON meetings USING gin (topics);
CREATE INDEX meetings_title_trgm ON meetings USING gin (title gin_trgm_ops);

-- 참석 범위 = "본인 회의" (UserContext.meeting_ids의 원천).
-- 초대 응답(rsvp)과 실제 출석(attended)은 다른 사실이라 나눈다 — RFC 5545의 ROLE / PARTSTAT를 따른다.
CREATE TABLE meeting_participants (
    meeting_id  bigint NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    person_id   bigint NOT NULL REFERENCES people(id),
    role        text NOT NULL DEFAULT 'required'
                CHECK (role IN ('chair', 'secretary', 'required', 'optional', 'observer')),
                -- CHAIR · REQ-PARTICIPANT · OPT-PARTICIPANT · NON-PARTICIPANT(배석) + 간사. 발표자는 agenda_items.presenter_id
    rsvp        text NOT NULL DEFAULT 'needs_action'
                CHECK (rsvp IN ('needs_action', 'accepted', 'declined', 'tentative')),
    attended    boolean,                       -- NULL = 회의 전 / 미기록. 회의록의 참석자 · 불참자 명단
    signed_at   timestamptz,                   -- 회의록 확인 · 서명 (formal 회의체는 출석자 전원)
    PRIMARY KEY (meeting_id, person_id),
    CHECK (signed_at IS NULL OR attended IS TRUE)
);
CREATE INDEX meeting_participants_person ON meeting_participants (person_id);

CREATE TABLE agenda_items (                    -- 안건 (seq = 진행 순서)
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    meeting_id    bigint NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    seq           smallint NOT NULL,
    title         text NOT NULL,
    description   text,
    presenter_id  bigint REFERENCES people(id),
    status        text NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'discussed', 'deferred')),
    carried_from  bigint REFERENCES agenda_items(id),   -- 이전 회의에서 이월된 안건
    UNIQUE (meeting_id, seq)
);
CREATE INDEX agenda_items_title_trgm ON agenda_items USING gin (title gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- 3. 자료 · 녹음
-- ---------------------------------------------------------------------------

CREATE TABLE documents (                       -- 사전 참고자료 · 발표자료 · 보고서 (파일 보관 경로가 정해지면)
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    meeting_id      bigint REFERENCES meetings(id) ON DELETE CASCADE,
    agenda_item_id  bigint REFERENCES agenda_items(id) ON DELETE SET NULL,
    title           text NOT NULL,
    kind            text NOT NULL DEFAULT 'reference'
                    CHECK (kind IN ('pre_read', 'presentation', 'report', 'reference')),
    uri             text,                      -- 파일 위치 (Open WebUI file id, 저장소 경로 등)
    content_text    text,                      -- 검색용 추출 텍스트
    classification  smallint NOT NULL DEFAULT 2 CHECK (classification BETWEEN 1 AND 4),
    created_by      text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE transcripts (                     -- 녹음 1개의 speech-service 결과
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    meeting_id   bigint NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    source_name  text,                         -- 회의녹음_뉴로엣지.wav
    duration_s   real,
    stt_engine   text,                         -- groq | local
    expires_at   timestamptz,                  -- 보존기간 만료: 삭제 작업이 지운다 (NULL = 미정)
    created_by   text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX transcripts_expires ON transcripts (expires_at) WHERE expires_at IS NOT NULL;

-- 발언 단위: "그때 누가 정확히 뭐라고 했나"를 다시 찾을 근거.
-- 화자 인식 오류는 맞는 결과처럼 보이므로, 자동 인식인지 사람이 확인했는지를 남긴다.
CREATE TABLE transcript_segments (
    transcript_id      bigint NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
    seq                int NOT NULL,
    start_s            real NOT NULL,
    end_s              real NOT NULL,
    speaker_label      text NOT NULL,          -- speech-service 라벨: "박지훈 (CFO)", "미확인 화자 1"
    speaker_score      real,                   -- voiceprint 코사인 점수 (speech-service segments[].score)
    person_id          bigint REFERENCES people(id),
    speaker_confirmed  boolean NOT NULL DEFAULT false,   -- 간사가 초안 검토에서 확인 · 수정함
    text               text NOT NULL,
    PRIMARY KEY (transcript_id, seq)
);
CREATE INDEX transcript_segments_text_trgm ON transcript_segments USING gin (text gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- 4. 회의록 · 결정사항
-- ---------------------------------------------------------------------------

CREATE TABLE minutes (                         -- 회의당 1개, 초안 → 확정
    meeting_id    bigint PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
    status        text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'final')),
    version       int NOT NULL DEFAULT 1,
    summary       text NOT NULL,
    body          text,                        -- 안건별 논의 경과 · 발언 요지 (서술)
    drafted_by    text,
    finalized_by  text,
    finalized_at  timestamptz,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CHECK ((status = 'final') = (finalized_at IS NOT NULL))
);
CREATE INDEX minutes_summary_trgm ON minutes USING gin (summary gin_trgm_ops);

CREATE TABLE minutes_revisions (               -- 확정본은 덮어쓰지 않고 정정한다: 버전별 스냅샷
    meeting_id  bigint NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    version     int NOT NULL,
    snapshot    jsonb NOT NULL,                -- 그 버전의 회의록 + 결정 + Action Item
    changed_by  text,
    reason      text,
    changed_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (meeting_id, version)
);

-- 결정 로그. kind:
--   decision    회의에서 합의한 결정                 resolution  표결로 의결한 안건 (votes_*, outcome)
--   criterion   이후 판단에 쓰는 기준 (가격 상한 등)    opinion     주요 발언 · 의견 (발언 요지)
--   issue       결론 내지 못한 쟁점 · 질문 → 다음 회의로, 해결되면 resolved_by_id
CREATE TABLE decisions (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    meeting_id            bigint NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    agenda_item_id        bigint REFERENCES agenda_items(id) ON DELETE SET NULL,
    seq                   smallint NOT NULL,
    kind                  text NOT NULL DEFAULT 'decision'
                          CHECK (kind IN ('decision', 'resolution', 'criterion', 'opinion', 'issue')),
    speaker_id            bigint REFERENCES people(id),
    speaker_label         text,                -- 사람으로 확정하지 못한 표기: "CFO", "미확인 화자 1"
    statement             text NOT NULL,
    rationale             text,                -- 근거: "왜 그렇게 정했나"
    outcome               text CHECK (outcome IN ('approved', 'amended', 'rejected', 'deferred')),  -- 원안가결 · 수정가결 · 부결 · 보류
    votes_for             smallint,
    votes_against         smallint,
    votes_abstain         smallint,
    supersedes_id         bigint REFERENCES decisions(id),   -- 이전 결정 · 기준을 바꾼 결정 (예: 가격 상한 2.5조 → 2.3조)
    resolved_by_id        bigint REFERENCES decisions(id),   -- issue를 해결한 결정
    source_transcript_id  bigint,              -- 근거 발언 (transcript가 만료되면 NULL)
    source_seq            int,
    UNIQUE (meeting_id, seq),
    FOREIGN KEY (source_transcript_id, source_seq)
        REFERENCES transcript_segments (transcript_id, seq) ON DELETE SET NULL,
    -- 의견 · 기준은 누가 말했는지가 핵심; 결정 · 의결 · 쟁점은 회의체의 것이라 발언자가 없어도 된다
    CHECK (kind IN ('decision', 'resolution', 'issue') OR speaker_id IS NOT NULL OR speaker_label IS NOT NULL),
    CHECK (kind = 'resolution' OR (outcome IS NULL AND votes_for IS NULL AND votes_against IS NULL AND votes_abstain IS NULL)),
    CHECK (resolved_by_id IS NULL OR kind = 'issue')
);
CREATE INDEX decisions_statement_trgm ON decisions USING gin (statement gin_trgm_ops);
CREATE INDEX decisions_supersedes ON decisions (supersedes_id);
CREATE INDEX decisions_open_issues ON decisions (meeting_id) WHERE kind = 'issue' AND resolved_by_id IS NULL;

CREATE TABLE decision_dissents (               -- 반대 · 기권 · 회피(이해관계)한 사람과 그 이유 — 무기명 표결이면 비워 둔다
    decision_id  bigint NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    person_id    bigint NOT NULL REFERENCES people(id),
    position     text NOT NULL CHECK (position IN ('against', 'abstain', 'recused')),
    reason       text,
    PRIMARY KEY (decision_id, person_id)
);

-- ---------------------------------------------------------------------------
-- 5. Action Item · 진행 이력
-- ---------------------------------------------------------------------------

-- 담당 · 기한이 없는 Action Item은 초안에서는 허용하고, 회의록 확정 때 Tool이 막는다.
CREATE TABLE action_items (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code                  text NOT NULL UNIQUE,     -- MTG-2026-10-25-01-A1
    meeting_id            bigint NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    decision_id           bigint REFERENCES decisions(id) ON DELETE SET NULL,
    text                  text NOT NULL,
    owner_person_id       bigint REFERENCES people(id),
    owner_unit_id         bigint REFERENCES org_units(id),
    due_date              date,
    status                text NOT NULL DEFAULT 'open'
                          CHECK (status IN ('open', 'in_progress', 'blocked', 'done', 'cancelled')),
    completed_at          timestamptz,
    source_transcript_id  bigint,              -- 근거 발언 (transcript가 만료되면 NULL)
    source_seq            int,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (source_transcript_id, source_seq)
        REFERENCES transcript_segments (transcript_id, seq) ON DELETE SET NULL,
    CHECK ((status = 'done') = (completed_at IS NOT NULL))
);
CREATE INDEX action_items_status_due ON action_items (status, due_date);
CREATE INDEX action_items_owner_person ON action_items (owner_person_id);
CREATE INDEX action_items_owner_unit ON action_items (owner_unit_id);

CREATE TABLE action_item_updates (             -- 진행 보고 · 상태 변경 (삭제하지 않는 이력)
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    action_item_id  bigint NOT NULL REFERENCES action_items(id) ON DELETE CASCADE,
    at              timestamptz NOT NULL DEFAULT now(),
    by_email        text,                      -- gateway principal
    old_status      text,
    new_status      text,
    note            text,
    reported_in     bigint REFERENCES meetings(id)   -- 다른 회의에서 보고된 진행이면 그 회의
);
CREATE INDEX action_item_updates_item ON action_item_updates (action_item_id, at);

-- ---------------------------------------------------------------------------
-- Phase 2 (전달 채널 · 임베딩 모델이 정해지면)
-- ---------------------------------------------------------------------------
-- reminders(id, kind 'pre_meeting_briefing'|'due_soon'|'overdue', meeting_id, action_item_id, person_id, remind_at, sent_at)
-- search_chunks(id, meeting_id, source_type, source_id, content, embedding vector(N), classification, allowed_groups)
--   "비슷한 논의 한 적 있어?"처럼 단어가 다른 질문, 2음절 검색어가 많은 대량 데이터용
