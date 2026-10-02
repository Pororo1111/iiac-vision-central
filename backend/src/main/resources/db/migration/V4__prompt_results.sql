-- 작업별 결과를 보존한다. 기존 migration과 과거 결과는 변경하지 않는다.
ALTER TABLE labeling_jobs ADD COLUMN result jsonb;
CREATE INDEX labeling_sample_history ON labeling_jobs(sample_id,created_at DESC,id);
CREATE INDEX training_samples_device_listing ON training_samples(device_id,created_at DESC,id);
