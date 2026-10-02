CREATE TABLE model_releases (
 id uuid PRIMARY KEY REFERENCES training_runs(id),
 sha256 varchar(64) NOT NULL,
 size_bytes bigint NOT NULL CHECK(size_bytes>0 AND size_bytes<=536870912),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE model_deployments (
 id uuid PRIMARY KEY,
 model_id uuid NOT NULL REFERENCES model_releases(id),
 device_id varchar(64) NOT NULL REFERENCES devices(device_id),
 status varchar(24) NOT NULL CHECK(status IN ('PENDING','DOWNLOADING','VALIDATING','APPLYING','APPLIED','FAILED','ROLLED_BACK')),
 active_sha256 varchar(64),
 message varchar(500) NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX deployment_one_active ON model_deployments(device_id) WHERE status IN ('PENDING','DOWNLOADING','VALIDATING','APPLYING');
