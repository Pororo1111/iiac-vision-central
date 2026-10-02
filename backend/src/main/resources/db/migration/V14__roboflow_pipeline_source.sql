-- 수집 이미지 없이 Roboflow 데이터셋만으로 학습하는 파이프라인 수집 방식.
ALTER TABLE pipelines DROP CONSTRAINT pipelines_source_kind_check;
ALTER TABLE pipelines ADD CONSTRAINT pipelines_source_kind_check CHECK (source_kind IN ('IOT','UPLOAD','EXISTING','ROBOFLOW'));
