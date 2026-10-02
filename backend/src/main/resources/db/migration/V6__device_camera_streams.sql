CREATE TABLE device_cameras (
    device_id VARCHAR(64) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    camera_id VARCHAR(64) NOT NULL,
    display_name VARCHAR(120) NOT NULL,
    stream_status VARCHAR(16) NOT NULL CHECK (stream_status IN ('STARTING','STREAMING','OFFLINE','ERROR')),
    width INTEGER,
    height INTEGER,
    fps DOUBLE PRECISION,
    last_stream_at TIMESTAMPTZ,
    last_error VARCHAR(300),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (device_id, camera_id)
);

CREATE INDEX device_cameras_status_updated_idx ON device_cameras (stream_status, updated_at DESC);
