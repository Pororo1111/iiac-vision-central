-- 중앙에서 IoT 감시 중지·재개를 요청하는 명령. 중앙은 IoT에 직접 접속하지 않고 heartbeat 응답으로 전달한다.
-- IoT는 처음 보는 command_id만 적용하므로 IoT 화면에서 직접 바꾼 상태를 오래된 명령이 덮어쓰지 않는다.
ALTER TABLE devices ADD COLUMN monitoring_action varchar(8) CHECK (monitoring_action IN ('PAUSE','RESUME'));
ALTER TABLE devices ADD COLUMN monitoring_command_id uuid;
ALTER TABLE devices ADD COLUMN monitoring_requested_at timestamptz;
ALTER TABLE devices ADD COLUMN monitoring_requested_by varchar(120);
