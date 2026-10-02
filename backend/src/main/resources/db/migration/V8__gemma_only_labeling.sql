-- 완료 상태는 사람의 승인과 구분한다. 과거 결과·검토 이력은 보존한다.
ALTER TABLE training_samples DROP CONSTRAINT training_samples_status_check;
ALTER TABLE training_samples ADD CONSTRAINT training_samples_status_check
  CHECK(status IN ('NEW','REVIEW','APPROVED','EXCLUDED','LABELED'));
-- 중단된 이전 워커의 결과가 새 흐름으로 들어오지 않도록 lease를 무효화한다.
UPDATE labeling_jobs SET status='FAILED',finished_at=now(),error='자동 라벨링 방식 변경으로 재등록'
WHERE status='RUNNING';
-- 과거 자동 라벨 결과는 새 Gemma 전용 정책으로 다시 처리한다.
UPDATE training_samples SET status='NEW',proposal=null,revision=revision+1
WHERE status='REVIEW' AND EXISTS (SELECT 1 FROM labeling_jobs j WHERE j.sample_id=training_samples.id AND j.kind='AUTO_LABEL');
INSERT INTO labeling_jobs(id,sample_id,sample_revision,reference_ids,instruction,kind)
SELECT gen_random_uuid(),s.id,s.revision,'[]'::jsonb,'Gemma 자동 라벨링','AUTO_LABEL'
FROM training_samples s WHERE s.status='NEW'
AND NOT EXISTS(SELECT 1 FROM labeling_jobs j WHERE j.sample_id=s.id AND j.status IN ('QUEUED','RUNNING'));
