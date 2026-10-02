-- Roboflow Universe 외부 데이터셋. 다운로드·클래스 자동 매핑은 호스트 AI 워커가 수행한다.
CREATE TABLE external_datasets (
  id uuid PRIMARY KEY,
  url varchar(300) NOT NULL,
  workspace varchar(100) NOT NULL,
  project varchar(100) NOT NULL,
  version integer CHECK (version IS NULL OR version > 0),
  status varchar(16) NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED','RUNNING','READY','FAILED')),
  name varchar(200),
  yaml_path varchar(500),
  classes jsonb,
  mapping jsonb,
  counts jsonb,
  lease_token uuid,
  lease_until timestamptz,
  error varchar(500),
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX external_datasets_queue ON external_datasets(created_at,id) WHERE status='QUEUED';

-- 학습 실행별 베이스 모델과 포함한 외부 데이터셋 스냅샷.
ALTER TABLE training_runs ADD COLUMN base_model varchar(80) NOT NULL DEFAULT 'default';
ALTER TABLE training_runs ADD COLUMN external jsonb NOT NULL DEFAULT '[]'::jsonb;

-- 관리자가 마법사로 만든 파이프라인. 수집·라벨링 대기 뒤 학습 실행(training_runs)을 만들어 잇는다.
CREATE TABLE pipelines (
  id uuid PRIMARY KEY,
  name varchar(100) NOT NULL,
  source_kind varchar(16) NOT NULL CHECK (source_kind IN ('IOT','UPLOAD','EXISTING')),
  source jsonb NOT NULL,
  external jsonb NOT NULL DEFAULT '[]'::jsonb,
  options jsonb NOT NULL,
  stage varchar(16) NOT NULL DEFAULT 'COLLECTING'
    CHECK (stage IN ('COLLECTING','LABELING','DATASET','RUN','FAILED','CANCELLED')),
  run_id uuid REFERENCES training_runs(id),
  sample_count integer NOT NULL DEFAULT 0,
  labeled_count integer NOT NULL DEFAULT 0,
  message varchar(500),
  created_by varchar(120) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX pipelines_listing ON pipelines(created_at DESC,id);
CREATE INDEX pipelines_active ON pipelines(created_at,id) WHERE stage IN ('COLLECTING','LABELING','DATASET');
