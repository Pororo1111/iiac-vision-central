-- 구간 판정에 쓰러진 사람(바닥에 누워 있거나 넘어진 자세) 수를 더한다(프롬프트 segment-v5).
-- Gemma는 작업자 보호구뿐 아니라 자세를 중점적으로 보고, 검색 조건 fallen=true는 fallen > 0인 구간을 찾는다.
ALTER TABLE observations ADD COLUMN fallen int CHECK (fallen >= 0);
