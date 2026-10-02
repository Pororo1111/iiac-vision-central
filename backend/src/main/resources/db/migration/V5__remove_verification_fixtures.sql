-- 사용자가 요청한 이전 개발 검증 데이터 정리. 실제 수집 데이터와 장치 인증 정보는 보존한다.
CREATE TABLE pending_media_deletions (sample_id uuid PRIMARY KEY);
INSERT INTO pending_media_deletions(sample_id)
SELECT id FROM training_samples WHERE title IN (
 '자동 검증용 이미지 · 학습 금지',
 '추론 테스트 기준 · 실제 회사 기준 아님',
 '실제 추론 연결 검증 · 학습 금지'
);
DELETE FROM annotation_reviews WHERE sample_id IN (SELECT sample_id FROM pending_media_deletions);
DELETE FROM labeling_jobs WHERE sample_id IN (SELECT sample_id FROM pending_media_deletions);
DELETE FROM training_samples WHERE id IN (SELECT sample_id FROM pending_media_deletions);
DELETE FROM events WHERE device_id='iot-001'
 AND ((camera_id='test-camera' AND zone_id='test-zone') OR (camera_id='sim-camera' AND zone_id='sim-zone'));
UPDATE devices SET name=device_id,model_name='',runtime_status='UNKNOWN',last_seen_at=NULL,registered_at=NULL
 WHERE name='통신 검증 장치' AND model_name='simulator / no camera';
