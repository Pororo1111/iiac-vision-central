CREATE TABLE admins (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(100) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE devices (
    device_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    model_name VARCHAR(200) NOT NULL DEFAULT '',
    runtime_status VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN'
        CHECK (runtime_status IN ('UNKNOWN','RUNNING','STOPPED','STALE','ERROR')),
    last_seen_at TIMESTAMPTZ,
    registered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE events (
    event_id UUID PRIMARY KEY,
    device_id VARCHAR(64) NOT NULL REFERENCES devices(device_id),
    camera_id VARCHAR(64) NOT NULL,
    zone_id VARCHAR(64) NOT NULL,
    type VARCHAR(16) NOT NULL CHECK (type IN ('INTRUSION_START','INTRUSION_END')),
    occurred_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    object_count INTEGER NOT NULL CHECK (object_count >= 0)
);
CREATE INDEX events_occurred_id_idx ON events (occurred_at DESC, event_id DESC);
CREATE INDEX events_device_occurred_id_idx ON events (device_id, occurred_at DESC, event_id DESC);
