CREATE TABLE training_samples (
  id uuid PRIMARY KEY,
  sha256 varchar(64) NOT NULL,
  title varchar(160) NOT NULL,
  mime varchar(30) NOT NULL,
  width integer NOT NULL CHECK (width > 0),
  height integer NOT NULL CHECK (height > 0),
  device_id varchar(64),
  camera_id varchar(64),
  captured_at timestamptz,
  is_reference boolean NOT NULL DEFAULT false,
  status varchar(16) NOT NULL DEFAULT 'NEW' CHECK (status IN ('NEW','REVIEW','APPROVED','EXCLUDED')),
  revision integer NOT NULL DEFAULT 0,
  proposal jsonb,
  annotation jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX training_samples_listing ON training_samples(created_at DESC,id);
CREATE TABLE labeling_jobs (
  id uuid PRIMARY KEY,
  sample_id uuid NOT NULL REFERENCES training_samples(id),
  sample_revision integer NOT NULL,
  reference_ids jsonb NOT NULL,
  instruction varchar(2000) NOT NULL,
  model varchar(80) NOT NULL DEFAULT 'gemma4:12b',
  status varchar(16) NOT NULL DEFAULT 'QUEUED' CHECK(status IN ('QUEUED','RUNNING','COMPLETED','FAILED')),
  lease_token uuid,
  lease_until timestamptz,
  error varchar(500),
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE UNIQUE INDEX labeling_one_live_job ON labeling_jobs(sample_id) WHERE status IN ('QUEUED','RUNNING');
CREATE INDEX labeling_queue ON labeling_jobs(created_at,id) WHERE status='QUEUED';
CREATE TABLE annotation_reviews (
  id bigserial PRIMARY KEY,
  sample_id uuid NOT NULL REFERENCES training_samples(id),
  revision integer NOT NULL,
  reviewer varchar(120) NOT NULL,
  status varchar(16) NOT NULL,
  annotation jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(sample_id,revision)
);
