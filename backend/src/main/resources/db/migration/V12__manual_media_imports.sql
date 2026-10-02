-- 관리자가 직접 올린 영상 파일·유튜브 링크의 프레임 추출 작업. 추출은 호스트 AI 워커가 수행한다.
CREATE TABLE media_imports (
  id uuid PRIMARY KEY,
  kind varchar(16) NOT NULL CHECK (kind IN ('VIDEO_FILE','YOUTUBE')),
  source varchar(300) NOT NULL,
  title varchar(160) NOT NULL,
  interval_seconds numeric(5,2) NOT NULL CHECK (interval_seconds BETWEEN 0.5 AND 60),
  max_frames integer NOT NULL CHECK (max_frames BETWEEN 1 AND 200),
  status varchar(16) NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED','RUNNING','COMPLETED','FAILED')),
  frames integer NOT NULL DEFAULT 0,
  lease_token uuid,
  lease_until timestamptz,
  error varchar(500),
  requested_by varchar(120) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX media_imports_queue ON media_imports(created_at,id) WHERE status='QUEUED';
CREATE INDEX media_imports_listing ON media_imports(created_at DESC,id);

-- 이미지 출처를 구분한다. 장치 정보가 없는 기존 이미지는 과거 직접 업로드 이미지다.
ALTER TABLE training_samples ADD COLUMN source varchar(16) NOT NULL DEFAULT 'IOT'
  CHECK (source IN ('IOT','UPLOAD','VIDEO_FILE','YOUTUBE'));
UPDATE training_samples SET source='UPLOAD' WHERE device_id IS NULL;
ALTER TABLE training_samples ADD COLUMN import_id uuid REFERENCES media_imports(id);
-- 워커가 같은 프레임을 재전송해도 하나만 보존한다.
CREATE UNIQUE INDEX training_samples_import_frame ON training_samples(import_id,sha256) WHERE import_id IS NOT NULL;
CREATE INDEX training_samples_upload_hash ON training_samples(sha256) WHERE source='UPLOAD';
