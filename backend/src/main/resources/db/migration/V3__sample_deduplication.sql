-- 같은 장치가 동일 촬영 이미지를 재전송하면 하나의 샘플만 보존한다.
CREATE UNIQUE INDEX training_samples_iot_identity
ON training_samples(device_id,camera_id,captured_at,sha256)
WHERE device_id IS NOT NULL;
