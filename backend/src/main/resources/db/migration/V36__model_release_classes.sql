-- 배포 후보의 클래스 표(2026-10-09). 중앙신호등(signal_tower)·스마트 콘(smart_cone)을 더한 6클래스 모델이 생겨
-- IoT 배포 정보의 classes를 고정 4클래스 대신 후보 모델의 실제 클래스 표로 보낸다. 이전 후보는 모두 4클래스다.
ALTER TABLE model_releases ADD COLUMN classes jsonb NOT NULL
  DEFAULT '["person","safety_vest","traffic_cone","hard_hat"]'::jsonb
  CHECK (jsonb_typeof(classes)='array' AND jsonb_array_length(classes) BETWEEN 1 AND 32);
