-- IoT가 heartbeat로 보고하는 현재 활성 모델 해시. 보고 전에는 NULL.
ALTER TABLE devices ADD COLUMN active_sha256 varchar(64) CHECK (active_sha256 ~ '^[0-9a-f]{64}$');

-- 장치별 배포 정책. 행이 없으면 PINNED(현재 모델 유지)로 본다.
-- PINNED: 고정 모델(pinned_model_id)만 허용, NULL이면 어떤 모델 배포도 막는다. AUTO: 평가 통과 모델의 자동 배포 허용.
CREATE TABLE device_model_policies (
  device_id varchar(64) PRIMARY KEY REFERENCES devices(device_id) ON DELETE CASCADE,
  mode varchar(8) NOT NULL CHECK (mode IN ('AUTO','PINNED')),
  pinned_model_id uuid REFERENCES model_releases(id),
  note varchar(200) NOT NULL DEFAULT '',
  updated_by varchar(120) NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (mode = 'PINNED' OR pinned_model_id IS NULL)
);

CREATE TABLE device_model_policy_history (
  id bigserial PRIMARY KEY,
  device_id varchar(64) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
  mode varchar(8) NOT NULL,
  pinned_model_id uuid,
  note varchar(200) NOT NULL DEFAULT '',
  changed_by varchar(120) NOT NULL,
  changed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX device_model_policy_history_device ON device_model_policy_history(device_id, changed_at DESC, id DESC);
