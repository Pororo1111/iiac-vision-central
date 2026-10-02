-- 장치 진단: IoT가 heartbeat로 보고하는 명령 반영 확인·오류·기동 시각과, 중앙이 heartbeat 응답으로 전달하는 재시작·로그 요청.
-- 중앙은 IoT에 직접 접속하지 않는다. 재시작·로그 요청은 요청 후 2분 안의 heartbeat에만 전달한다.
ALTER TABLE devices ADD COLUMN monitoring_applied_id uuid;
ALTER TABLE devices ADD COLUMN last_error varchar(500);
ALTER TABLE devices ADD COLUMN iot_started_at timestamptz;

ALTER TABLE devices ADD COLUMN restart_target varchar(8) CHECK (restart_target IN ('WORKER','API','ALL'));
ALTER TABLE devices ADD COLUMN restart_command_id uuid;
ALTER TABLE devices ADD COLUMN restart_requested_at timestamptz;
ALTER TABLE devices ADD COLUMN restart_requested_by varchar(120);
ALTER TABLE devices ADD COLUMN restart_applied_id uuid;

ALTER TABLE devices ADD COLUMN log_request_id uuid;
ALTER TABLE devices ADD COLUMN log_requested_at timestamptz;
ALTER TABLE devices ADD COLUMN log_lines int CHECK (log_lines BETWEEN 20 AND 2000);

-- 장치별 마지막으로 받은 로그만 보관한다(서비스별 한 행).
CREATE TABLE device_logs (
  device_id varchar(64) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
  service varchar(64) NOT NULL,
  request_id uuid NOT NULL,
  content text NOT NULL,
  collected_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (device_id, service)
);
