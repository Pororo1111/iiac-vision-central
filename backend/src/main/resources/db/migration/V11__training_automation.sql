-- NULL은 단계별 수동 실행, 값이 있으면 구성→학습→평가→후보 등록→배포 요청을 자동으로 잇는다.
ALTER TABLE training_runs ADD COLUMN automation jsonb;
