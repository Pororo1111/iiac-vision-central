-- 파이프라인 이름은 학습이 끝난 뒤 Gemma가 추천한 이름으로 자동 설정한다. 기존 항목은 완료 상태로 둔다.
ALTER TABLE pipelines ADD COLUMN name_status varchar(16) NOT NULL DEFAULT 'DONE'
  CHECK (name_status IN ('PENDING','GENERATING','DONE'));
ALTER TABLE pipelines ADD COLUMN name_lease_until timestamptz;
CREATE INDEX pipelines_naming ON pipelines(created_at,id) WHERE name_status<>'DONE';
