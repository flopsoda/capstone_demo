-- 005: 출석 확인(QR)과 회의별 발언자 라벨.
-- meeting_store.migrate()가 한 번만 적용한다 (schema_migrations). 이 파일은 고치지 말고 다음 번호로 추가한다.

-- ---------------------------------------------------------------------------
-- '화자 A'처럼 목소리가 등록되지 않은 발언자는 그 녹음(회의) 안에서만 한 사람이다.
-- 지금까지는 이름이 같은 people 한 줄을 모든 회의가 같이 써서, 다른 회의의 '화자 A'가 같은 사람으로 묶였다.
--   speaker_meeting_id  그 라벨이 가리키는 회의. 실제 사람은 NULL
-- ---------------------------------------------------------------------------
ALTER TABLE people ADD COLUMN speaker_meeting_id bigint REFERENCES meetings(id) ON DELETE CASCADE;
CREATE INDEX people_speaker_meeting ON people (speaker_meeting_id) WHERE speaker_meeting_id IS NOT NULL;

-- 이미 여러 회의가 같이 쓰는 라벨은 회의마다 한 줄씩으로 나눈다 (첫 회의가 원래 줄을 가진다).
DO $$
DECLARE
    p record;
    m bigint;
    first boolean;
    clone bigint;
BEGIN
    FOR p IN SELECT id, display_name FROM people
             WHERE speaker_meeting_id IS NULL AND display_name ~ '^(화자 [A-Z]{1,2}|미확인 화자( [0-9]+)?)$' LOOP
        first := true;
        FOR m IN
            SELECT meeting_id FROM meeting_participants WHERE person_id = p.id
            UNION SELECT meeting_id FROM action_items WHERE owner_person_id = p.id
            UNION SELECT meeting_id FROM decisions WHERE speaker_id = p.id
            UNION SELECT d.meeting_id FROM decision_dissents x JOIN decisions d ON d.id = x.decision_id WHERE x.person_id = p.id
            UNION SELECT t.meeting_id FROM transcript_segments s JOIN transcripts t ON t.id = s.transcript_id WHERE s.person_id = p.id
            ORDER BY 1
        LOOP
            IF first THEN
                UPDATE people SET speaker_meeting_id = m WHERE id = p.id;
                first := false;
            ELSE
                INSERT INTO people (display_name, speaker_meeting_id) VALUES (p.display_name, m) RETURNING id INTO clone;
                UPDATE meeting_participants SET person_id = clone WHERE person_id = p.id AND meeting_id = m;
                UPDATE action_items SET owner_person_id = clone WHERE owner_person_id = p.id AND meeting_id = m;
                UPDATE decisions SET speaker_id = clone WHERE speaker_id = p.id AND meeting_id = m;
                UPDATE decision_dissents x SET person_id = clone FROM decisions d
                    WHERE d.id = x.decision_id AND x.person_id = p.id AND d.meeting_id = m;
                UPDATE transcript_segments s SET person_id = clone FROM transcripts t
                    WHERE t.id = s.transcript_id AND s.person_id = p.id AND t.meeting_id = m;
            END IF;
        END LOOP;
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- 출석 확인: 녹음을 올리면 화면에 QR을 띄우고(30초마다 바뀜), 참석자가 휴대폰으로 찍으면 회사 계정으로 출석한다.
-- 말하지 않은 참석자도 회의록 참석자가 되고, 이름 · 이메일을 알게 되어 할 일을 맡기고 메일을 보낼 수 있다.
-- ---------------------------------------------------------------------------
CREATE TABLE checkin_sessions (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code         text NOT NULL UNIQUE,         -- CHK-7F3K2Q (회의록 요청 문장에 함께 적힌다)
    secret       text NOT NULL,                -- QR의 30초 코드를 만드는 키 (화면 밖으로 나가지 않음)
    meeting_id   bigint REFERENCES meetings(id) ON DELETE SET NULL,   -- 회의록이 저장되면 연결
    source_name  text,                         -- 함께 올린 녹음 파일 이름
    created_by   text NOT NULL,                -- QR을 연 사람 (이메일)
    created_at   timestamptz NOT NULL DEFAULT now(),
    closes_at    timestamptz NOT NULL          -- 이 뒤로는 출석을 받지 않는다
);
CREATE INDEX checkin_sessions_creator ON checkin_sessions (created_by, created_at DESC);

CREATE TABLE checkin_attendees (
    session_id        bigint NOT NULL REFERENCES checkin_sessions(id) ON DELETE CASCADE,
    email             text NOT NULL,           -- 로그인한 회사 계정
    name              text NOT NULL,
    user_id           text,                    -- Keycloak 사용자 id (목소리를 익힐 때 그 사람의 voiceprint 키)
    checked_in_at     timestamptz NOT NULL DEFAULT now(),
    voice_consent_at  timestamptz,             -- 이 회의 녹음에서 목소리 특징값을 익혀도 된다는 동의 (민감정보, 본인만)
    PRIMARY KEY (session_id, email)
);

ALTER TABLE meeting_participants ADD COLUMN checked_in_at timestamptz;   -- QR로 출석한 시각 (NULL = 출석 확인 없음)
