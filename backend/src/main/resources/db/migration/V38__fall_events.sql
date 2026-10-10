-- 쓰러짐 이벤트(2026-10-10). IoT(Jetson)가 포즈 모델+규칙으로 쓰러짐을 확정하면 FALL_START, 일어나거나 사라지면 FALL_END를 보낸다.
-- FALL_START의 eventId를 같은 UUID의 탐지 배치(batchId)로 보내 원본 사진을 붙이고, 중앙 Gemma 장면 판정(observations.fallen)이
-- 2차 확인이 된다. 쓰러짐은 카메라 전체 판정이라 zone_id는 빈 문자열로 저장할 수 있다.
ALTER TABLE events DROP CONSTRAINT events_type_check;
ALTER TABLE events ADD CONSTRAINT events_type_check
  CHECK (type IN ('INTRUSION_START','INTRUSION_END','FALL_START','FALL_END'));

-- 관리자 확인(알림 닫기). 이벤트 본문은 IoT 원본 그대로 두고 확인 기록만 따로 둔다.
CREATE TABLE event_acks (
  event_id uuid PRIMARY KEY REFERENCES events(event_id) ON DELETE CASCADE,
  acked_by varchar(64) NOT NULL,
  acked_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX events_fall_start_idx ON events (occurred_at DESC, event_id DESC) WHERE type='FALL_START';
