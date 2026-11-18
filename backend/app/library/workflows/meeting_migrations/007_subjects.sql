-- 007: 회의의 주제(subject) — 회의체 · 주를 넘어 이어지는 사업 · 안건 (예: 천안 신공장, 뉴로엣지 인수).
-- meeting_store.migrate()가 한 번만 적용한다 (schema_migrations). 이 파일은 고치지 말고 다음 번호로 추가한다.
-- 회의 업무의 관제 화면은 주제마다 한 칸(진행 중인 결정 · 전제 · 할 일 · 다음 회의)을 보여 준다 (meeting_control.py).
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS subject text;
CREATE INDEX IF NOT EXISTS meetings_subject ON meetings (subject, starts_at);
