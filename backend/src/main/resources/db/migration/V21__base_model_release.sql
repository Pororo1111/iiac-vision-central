-- 현장 기본 모델(ppe4 등)을 학습 실행 없이 배포 후보로 등록할 수 있게 한다.
-- RUN 후보의 학습 실행 삭제는 애플리케이션이 MODEL_REGISTERED로 막는다.
ALTER TABLE model_releases DROP CONSTRAINT model_releases_id_fkey;
ALTER TABLE model_releases ADD COLUMN kind varchar(8) NOT NULL DEFAULT 'RUN' CHECK (kind IN ('RUN','BASE'));
ALTER TABLE model_releases ADD COLUMN label varchar(100);
CREATE UNIQUE INDEX model_releases_base_sha ON model_releases(sha256) WHERE kind = 'BASE';
