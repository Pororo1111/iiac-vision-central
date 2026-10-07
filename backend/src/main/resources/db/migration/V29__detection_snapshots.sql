-- 상시 송출 영상 검색: IoT가 탐지 배치와 함께 보내는 오버레이 없는 원본 프레임(최대 4장)으로 Gemma 구간 판정을 한다.
-- 송출 영상에는 IoT가 그린 박스·라벨이 있어 Gemma가 그 글자를 근거로 삼을 수 있기 때문이다. 송출 녹화는 재생용으로 남는다.

-- 배치 출처: IOT(장치가 보낸 배치), STREAM(중앙 송출 녹화 색인 워커가 만든 배치).
-- 장치가 배치를 보내기 시작하면 색인 워커는 그 카메라의 재탐지를 멈추고 녹화 등록만 한다.
ALTER TABLE detection_batches ADD COLUMN source varchar(8) NOT NULL DEFAULT 'IOT' CHECK (source IN ('IOT', 'STREAM'));
-- 기존 배치 중 송출 녹화 구간과 겹치는 것은 색인 워커가 만든 배치다(실제 IoT는 V29 전까지 배치를 보내지 않았다).
UPDATE detection_batches b SET source='STREAM' WHERE EXISTS(
  SELECT 1 FROM recordings r WHERE r.source='STREAM' AND r.device_id=b.device_id AND r.camera_id=b.camera_id
    AND r.started_at<b.ended_at AND r.ended_at>b.started_at);
CREATE INDEX detection_batches_source ON detection_batches(device_id, camera_id, source, received_at);

-- 원본 프레임. 파일은 MEDIA_ROOT/detection-snapshots/{batch_id}/{captured_at 마이크로초}.jpg다.
-- captured_at은 배치 frames[].at 중 하나다. 같은 배치·시각에 다른 이미지는 받지 않는다.
CREATE TABLE detection_snapshots (
  batch_id uuid NOT NULL REFERENCES detection_batches(batch_id) ON DELETE CASCADE,
  captured_at timestamptz NOT NULL,
  sha256 char(64) NOT NULL,
  size_bytes int NOT NULL CHECK (size_bytes > 0),
  width int NOT NULL,
  height int NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (batch_id, captured_at)
);

-- 구간 분석이 원본 프레임을 기다리는 조건. snapshot_expected장이 모두 오거나 snapshot_due가 지나면 가져간다.
-- 기한 안에 하나도 오지 않으면 송출 녹화(오버레이 포함)에서 프레임을 뽑고 frame_source=RECORDING으로 남긴다.
ALTER TABLE observations ADD COLUMN snapshot_expected int NOT NULL DEFAULT 0;
ALTER TABLE observations ADD COLUMN snapshot_due timestamptz;
ALTER TABLE observations ADD COLUMN frame_source varchar(12) CHECK (frame_source IN ('SNAPSHOT', 'RECORDING'));
