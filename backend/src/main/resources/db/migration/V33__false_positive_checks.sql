-- 오탐 확인: 장치가 보낸 원본 프레임에서 YOLO가 hard_hat·safety_vest로 잡은 박스를 Gemma가 하나씩 다시 본다.
-- 오탐이 하나 이상이고 애매한 판정이 없으면 워커가 그 원본과 고친 라벨을 누적 데이터셋(local:gemma-fp)에 넣는다.
-- 추론·파일 쓰기는 호스트 워커(ai-worker/observer.py)가 하고, 중앙은 대기열·결과·데이터셋 집계만 한다.
CREATE TABLE fp_checks (
  id uuid PRIMARY KEY,
  batch_id uuid NOT NULL,
  captured_at timestamptz NOT NULL,
  device_id varchar(64) NOT NULL,
  camera_id varchar(64) NOT NULL,
  status varchar(8) NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED', 'RUNNING', 'DONE', 'FAILED')),
  attempts int NOT NULL DEFAULT 0,
  lease_token uuid,
  lease_until timestamptz,
  error varchar(500),
  model varchar(64),
  prompt_version varchar(32),
  -- [{index, cls, score, box, verdict: real|false|unsure, seen, reason}]
  verdicts jsonb,
  false_count int,
  unsure_count int,
  -- 데이터셋에 넣은 경우의 파일 이름·split. 넣지 않은 이유는 skip_reason(no_false·unsure).
  dataset_name varchar(200),
  dataset_split varchar(8) CHECK (dataset_split IN ('train', 'val')),
  dataset_boxes int,
  skip_reason varchar(32),
  elapsed_ms int,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  UNIQUE (batch_id, captured_at),
  FOREIGN KEY (batch_id, captured_at) REFERENCES detection_snapshots(batch_id, captured_at) ON DELETE CASCADE
);
CREATE INDEX fp_checks_queue ON fp_checks(status, created_at, id);
CREATE INDEX fp_checks_recent ON fp_checks(created_at DESC, id);

-- 이미 받은 원본 프레임 중 그 프레임의 YOLO 결과에 보호구가 있는 것도 확인 대상으로 등록한다.
INSERT INTO fp_checks(id, batch_id, captured_at, device_id, camera_id)
SELECT gen_random_uuid(), s.batch_id, s.captured_at, b.device_id, b.camera_id
FROM detection_snapshots s JOIN detection_batches b ON b.batch_id = s.batch_id
WHERE EXISTS (
  SELECT 1 FROM jsonb_array_elements(b.payload -> 'frames') f, jsonb_array_elements(f -> 'objects') o
  WHERE (f ->> 'at')::timestamptz = s.captured_at AND o ->> 'cls' IN ('hard_hat', 'safety_vest'));
