-- 라벨링 결과 객체가 하나도 없는 이미지는 학습에 도움이 되지 않으므로 삭제하고 집계용 기록만 남긴다.
CREATE TABLE discarded_samples (
  id uuid PRIMARY KEY,
  source varchar(16) NOT NULL,
  device_id varchar(64),
  reason varchar(40) NOT NULL,
  discarded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX discarded_samples_recent ON discarded_samples(discarded_at DESC);

-- 여러 IoT가 보낸 이미지로 정해진 시각(한국 시간)에 도는 전역 자동 파이프라인 설정. 한 행만 둔다.
CREATE TABLE auto_pipeline_settings (
  id smallint PRIMARY KEY CHECK (id = 1),
  enabled boolean NOT NULL DEFAULT true,
  run_at time NOT NULL DEFAULT '02:00',
  min_samples integer NOT NULL DEFAULT 50 CHECK (min_samples BETWEEN 1 AND 1000),
  epochs integer NOT NULL DEFAULT 100 CHECK (epochs BETWEEN 1 AND 100),
  base_policy varchar(16) NOT NULL DEFAULT 'LATEST' CHECK (base_policy IN ('LATEST','DEFAULT')),
  last_run_date date,
  last_result varchar(300),
  updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO auto_pipeline_settings(id) VALUES (1);

ALTER TABLE pipelines DROP CONSTRAINT pipelines_source_kind_check;
ALTER TABLE pipelines ADD CONSTRAINT pipelines_source_kind_check CHECK (source_kind IN ('IOT','UPLOAD','EXISTING','ROBOFLOW','AUTO'));
