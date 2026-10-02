-- 영상 검색 화면(에이전트형 UI): 질문 해석 과정 기록과 구간 대표 이미지.
-- trace: 워커가 Gemma에 준 입력 요약·원본 응답·코드로 계산한 기간. 화면의 "중간 단계"에 그대로 보여 준다.
ALTER TABLE search_questions ADD COLUMN trace jsonb;
-- 관리자가 답을 기다리다 중지한 질문. 워커가 늦게 답을 보내도 임대가 없어 저장되지 않는다.
ALTER TABLE search_questions DROP CONSTRAINT search_questions_status_check;
ALTER TABLE search_questions ADD CONSTRAINT search_questions_status_check
  CHECK (status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELED'));
-- 구간 분석 때 박스·추적 ID를 그린 대표 프레임을 MEDIA_ROOT/observations/{batch_id}.jpg로 저장했는지.
ALTER TABLE observations ADD COLUMN has_thumbnail boolean NOT NULL DEFAULT false;
