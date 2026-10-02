-- 영상 분석·검색 P1-a: 원본 녹화 파일 등록. 재생 위치 = 요청 시각 - 촬영 시작 시각.
-- 테스트 영상은 가상 촬영 시작 시각으로 등록한다. MediaMTX 녹화 연결(P1-b)은 같은 테이블을 사용할 예정이다.
CREATE TABLE recordings (
  id uuid PRIMARY KEY,
  device_id varchar(64) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
  camera_id varchar(64) NOT NULL,
  started_at timestamptz NOT NULL,
  ended_at timestamptz NOT NULL,
  source varchar(16) NOT NULL DEFAULT 'FILE' CHECK (source IN ('FILE')),
  sha256 char(64) NOT NULL,
  size_bytes bigint NOT NULL CHECK (size_bytes > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (ended_at > started_at AND ended_at <= started_at + interval '1 day'),
  UNIQUE (device_id, camera_id, started_at)
);
CREATE INDEX recordings_lookup ON recordings(device_id, camera_id, started_at DESC, id);
