CREATE TABLE training_runs (
 id uuid PRIMARY KEY,
 name varchar(100) NOT NULL,
 state varchar(24) NOT NULL,
 snapshot jsonb NOT NULL,
 summary jsonb NOT NULL DEFAULT '{}'::jsonb,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE training_tasks (
 id uuid PRIMARY KEY,
 run_id uuid NOT NULL REFERENCES training_runs(id),
 action varchar(16) NOT NULL CHECK(action IN ('PREPARE','TRAIN','EVALUATE')),
 status varchar(16) NOT NULL CHECK(status IN ('QUEUED','RUNNING','COMPLETED','FAILED')),
 parameters jsonb NOT NULL,
 lease_token uuid,
 lease_until timestamptz,
 error varchar(500),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX training_one_active ON training_tasks ((true)) WHERE status IN ('QUEUED','RUNNING');
