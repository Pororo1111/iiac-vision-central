-- 장치 송출(MediaMTX fMP4 분할 녹화)을 원본 녹화로 등록할 수 있게 출처 STREAM을 추가한다.
ALTER TABLE recordings DROP CONSTRAINT recordings_source_check;
ALTER TABLE recordings ADD CONSTRAINT recordings_source_check CHECK (source IN ('FILE', 'STREAM'));
