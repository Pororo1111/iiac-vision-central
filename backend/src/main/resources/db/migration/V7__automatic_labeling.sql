-- 자유 프롬프트 분석과 수집 후 자동 박스 라벨링을 구분한다.
ALTER TABLE labeling_jobs ADD COLUMN kind varchar(20) NOT NULL DEFAULT 'ANALYSIS'
  CHECK (kind IN ('ANALYSIS','AUTO_LABEL'));
-- 아직 작업을 요청하지 않은 기존 수집 이미지도 자동 라벨링 대상으로 등록한다.
INSERT INTO labeling_jobs(id,sample_id,sample_revision,reference_ids,instruction,kind)
SELECT gen_random_uuid(),s.id,s.revision,'[]'::jsonb,'수집 이미지 자동 라벨링','AUTO_LABEL'
FROM training_samples s WHERE s.status='NEW'
AND NOT EXISTS (SELECT 1 FROM labeling_jobs j WHERE j.sample_id=s.id);
