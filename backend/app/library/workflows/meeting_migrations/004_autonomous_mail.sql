-- 004: 메일은 회의지원 Agent가 받는 사람의 주소를 스스로 찾아 넣고 바로 보낸다 (사람의 승인 단계 없음).
--   주소를 찾는 순서: people.email → 로그인 때 등록한 이메일(Keycloak 디렉터리) → 임직원 · 외부 연락처(staff_directory)
--   needs_address  어디에서도 찾지 못함 → 매일 아침 다시 찾고, 찾으면 바로 발송 대기
--   approved       발송 대기 (approved_by = 'agent') → sent | failed
--   pending        더 이상 만들지 않음 (이 migration 전에 승인을 기다리던 메일은 발송 대기로 넘긴다)

-- ---------------------------------------------------------------------------
-- 임직원 · 외부 연락처: 인사 시스템 · 주소록에서 받아 두는 명부 (데모는 system의 data/meetings.json "directory").
-- 로그인한 적 없는 사람(외부 자문위원, 계열사 임원)도 여기서 주소를 찾는다.
-- ---------------------------------------------------------------------------
CREATE TABLE staff_directory (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          text NOT NULL,               -- 박지훈
    title         text,                        -- CFO, 그룹 전략실장, 자문위원
    department    text,                        -- 재무본부
    organization  text,                        -- 소속 회사 · 기관 (K전자, OO회계법인)
    email         text NOT NULL UNIQUE,
    is_external   boolean NOT NULL DEFAULT false
);
CREATE INDEX staff_directory_name ON staff_directory (lower(replace(name, ' ', '')));

UPDATE outbox SET status = 'approved', approved_by = 'agent', approved_at = now() WHERE status = 'pending';
