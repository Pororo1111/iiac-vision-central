ALTER TABLE model_deployments DROP CONSTRAINT model_deployments_status_check;
ALTER TABLE model_deployments ADD CONSTRAINT model_deployments_status_check
 CHECK(status IN ('PENDING','DOWNLOADING','VALIDATING','APPLYING','APPLIED','FAILED','ROLLED_BACK','CANCELED'));
