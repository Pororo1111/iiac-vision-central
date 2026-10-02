-- 영상 분석·검색 얇은 한 바퀴(P3 최소·P4 최소·P5 최소).
-- 탐지 배치 하나 = 10초 구간 분석 작업 하나. 해당 시각의 원본 녹화가 있어야 워커가 가져간다.
CREATE TABLE observations (
  batch_id uuid PRIMARY KEY REFERENCES detection_batches(batch_id) ON DELETE CASCADE,
  device_id varchar(64) NOT NULL,
  camera_id varchar(64) NOT NULL,
  started_at timestamptz NOT NULL,
  ended_at timestamptz NOT NULL,
  status varchar(12) NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'SKIPPED', 'FAILED')),
  attempts int NOT NULL DEFAULT 0,
  lease_token uuid,
  lease_until timestamptz,
  summary text,
  skip_reason varchar(200),
  error varchar(500),
  model varchar(64),
  prompt_version varchar(32),
  frame_count int,
  elapsed_ms int,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX observations_queue ON observations(status, created_at, batch_id);
CREATE INDEX observations_time ON observations(device_id, camera_id, started_at, batch_id);

-- 구간 안 사람(추적 ID)별 Gemma 판정. yolo_*는 P2 연결 규칙 결과를 함께 보관한다.
CREATE TABLE observation_persons (
  batch_id uuid NOT NULL REFERENCES observations(batch_id) ON DELETE CASCADE,
  track_id varchar(32) NOT NULL,
  helmet varchar(8) NOT NULL CHECK (helmet IN ('present', 'absent', 'unknown')),
  vest varchar(8) NOT NULL CHECK (vest IN ('present', 'absent', 'unknown')),
  evidence varchar(1000) NOT NULL,
  yolo_helmet varchar(8),
  yolo_vest varchar(8),
  PRIMARY KEY (batch_id, track_id)
);
CREATE INDEX observation_persons_state ON observation_persons(helmet, vest);

-- 자연어 질문 대화. Gemma는 질문을 검색 조건 JSON으로만 바꾸고 검색은 중앙 SQL이 한다.
CREATE TABLE search_conversations (
  id uuid PRIMARY KEY,
  created_by varchar(64) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE search_questions (
  id uuid PRIMARY KEY,
  conversation_id uuid NOT NULL REFERENCES search_conversations(id) ON DELETE CASCADE,
  seq int NOT NULL,
  message varchar(500) NOT NULL,
  status varchar(12) NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')),
  lease_token uuid,
  lease_until timestamptz,
  filter jsonb,
  reply varchar(500),
  error varchar(500),
  elapsed_ms int,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  UNIQUE (conversation_id, seq)
);
CREATE INDEX search_questions_queue ON search_questions(status, created_at, id);
