-- Roboflow 불러오기 진행 단계(EXPORT·DOWNLOAD·EXTRACT·VALIDATE)와 내려받기 진행률(%).
ALTER TABLE external_datasets ADD COLUMN phase varchar(16) CHECK (phase IN ('EXPORT','DOWNLOAD','EXTRACT','VALIDATE'));
ALTER TABLE external_datasets ADD COLUMN progress smallint CHECK (progress BETWEEN 0 AND 100);
