-- 카메라 원본 장치·해상도(2026-10-08). 같은 camera_id(usb-0)에 다른 웹캠이 꽂히면 원본 비율·크기가 바뀐다
-- (Insta360 16:9 → FHD Camera 4:3). width/height는 IoT 송출 해상도라 원본을 알 수 없어 선택 필드를 따로 받는다.
ALTER TABLE device_cameras ADD COLUMN source_width int CHECK (source_width BETWEEN 1 AND 16384);
ALTER TABLE device_cameras ADD COLUMN source_height int CHECK (source_height BETWEEN 1 AND 16384);
ALTER TABLE device_cameras ADD COLUMN source_device varchar(120);

-- 원본 장치·해상도가 바뀔 때마다 한 행. 같은 값이 계속 보고되면 last_seen_at만 갱신한다.
CREATE TABLE camera_source_history (
  id bigserial PRIMARY KEY,
  device_id varchar(64) NOT NULL,
  camera_id varchar(64) NOT NULL,
  source_width int NOT NULL,
  source_height int NOT NULL,
  source_device varchar(120),
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (device_id, camera_id) REFERENCES device_cameras(device_id, camera_id) ON DELETE CASCADE
);
CREATE INDEX camera_source_history_latest ON camera_source_history(device_id, camera_id, first_seen_at DESC, id DESC);

-- 수집 이미지 크기 이력을 카메라별로 시각 순으로 훑는다(원본 사진은 detection_batches_time을 쓴다).
CREATE INDEX training_samples_camera ON training_samples(device_id, camera_id, captured_at);
