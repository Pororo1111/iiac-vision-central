-- 관리자가 카메라 보기를 열어 둔 동안에만 IoT가 송출하도록 하는 송출 임대.
-- 장치·카메라당 임대 하나에 시청자(관리자·창)별 만료를 합친다. 임대 만료는 시청자 만료 중 가장 늦은 값이다.
CREATE TABLE stream_leases (
    device_id VARCHAR(64) NOT NULL,
    camera_id VARCHAR(64) NOT NULL,
    request_id UUID NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    requested_by VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (device_id, camera_id),
    FOREIGN KEY (device_id, camera_id) REFERENCES device_cameras(device_id, camera_id) ON DELETE CASCADE
);

CREATE INDEX stream_leases_device_expires_idx ON stream_leases (device_id, expires_at DESC);

CREATE TABLE stream_lease_viewers (
    device_id VARCHAR(64) NOT NULL,
    camera_id VARCHAR(64) NOT NULL,
    admin_username VARCHAR(64) NOT NULL,
    viewer_id UUID NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (device_id, camera_id, admin_username, viewer_id),
    FOREIGN KEY (device_id, camera_id) REFERENCES device_cameras(device_id, camera_id) ON DELETE CASCADE
);
