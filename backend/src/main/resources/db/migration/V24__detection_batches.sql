-- 영상 분석·검색 P2: IoT(현재는 테스트 영상 모의 IoT)가 보낸 YOLO·추적 탐지 배치와 사람별 보호구 연결 요약.
-- 원본 프레임 탐지는 payload에 보존해 연결 규칙을 바꾸면 다시 계산할 수 있게 한다.
-- 연결 규칙은 YOLO 근거만으로 present/unknown만 만든다. 미탐지를 absent로 바꾸지 않는다.
CREATE TABLE detection_batches (
  batch_id uuid PRIMARY KEY,
  device_id varchar(64) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
  camera_id varchar(64) NOT NULL,
  session_id uuid NOT NULL,
  started_at timestamptz NOT NULL,
  ended_at timestamptz NOT NULL,
  model_name varchar(128) NOT NULL,
  tracker varchar(32) NOT NULL,
  rule_version varchar(32) NOT NULL,
  frame_count int NOT NULL,
  cut_count int NOT NULL,
  untracked_persons int NOT NULL,
  unassigned_hard_hats int NOT NULL,
  unassigned_vests int NOT NULL,
  payload jsonb NOT NULL,
  payload_sha256 char(64) NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  CHECK (ended_at > started_at AND ended_at <= started_at + interval '60 seconds')
);
CREATE INDEX detection_batches_time ON detection_batches(device_id, camera_id, started_at, batch_id);

CREATE TABLE detection_tracks (
  batch_id uuid NOT NULL REFERENCES detection_batches(batch_id) ON DELETE CASCADE,
  track_id varchar(32) NOT NULL,
  device_id varchar(64) NOT NULL,
  camera_id varchar(64) NOT NULL,
  session_id uuid NOT NULL,
  first_seen timestamptz NOT NULL,
  last_seen timestamptz NOT NULL,
  frames int NOT NULL CHECK (frames > 0),
  max_score real NOT NULL,
  helmet_frames int NOT NULL,
  vest_frames int NOT NULL,
  helmet varchar(8) NOT NULL CHECK (helmet IN ('present', 'unknown')),
  vest varchar(8) NOT NULL CHECK (vest IN ('present', 'unknown')),
  PRIMARY KEY (batch_id, track_id),
  CHECK (last_seen >= first_seen)
);
CREATE INDEX detection_tracks_time ON detection_tracks(device_id, camera_id, first_seen, batch_id, track_id);
