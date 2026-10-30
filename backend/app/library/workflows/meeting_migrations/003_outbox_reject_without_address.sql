-- 003: 주소를 찾지 못한(needs_address) 메일도 반려 · 대체(rejected)할 수 있게 한다.
-- 002의 이름 없는 CHECK(outbox_check: needs_address가 아니면 to_email 필수)는 rejected로 바꾸는 것까지 막았다.
ALTER TABLE outbox DROP CONSTRAINT IF EXISTS outbox_check;
ALTER TABLE outbox ADD CONSTRAINT outbox_address CHECK (status IN ('needs_address', 'rejected') OR to_email IS NOT NULL);
