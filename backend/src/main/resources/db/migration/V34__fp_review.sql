-- 오탐 확인 관리자 검토(시연 계획 2026-10-07). Gemma는 판정만 하고 데이터셋에는 쓰지 않는다.
-- 오탐·애매 판정이 있는 프레임은 PENDING으로 관리자 확인을 기다리고, 관리자가 박스별로 정탐·오탐을 정해 승인하면
-- 서버가 원본과 고친 라벨을 누적 데이터셋 local:admin-fp에 넣는다. 반려는 데이터셋에 넣지 않는다.
-- (2026-10-07 검증에서 Gemma가 남색 공항 조끼·실제 안전모를 오탐으로 확정해 사람 확인 없이 넣을 수 없었다.)
ALTER TABLE fp_checks ADD COLUMN review_status varchar(12)
  CHECK (review_status IN ('NOT_NEEDED', 'PENDING', 'APPROVED', 'REJECTED'));
-- 승인 시 관리자가 오탐으로 정한 보호구 박스 index 목록
ALTER TABLE fp_checks ADD COLUMN removed jsonb;
ALTER TABLE fp_checks ADD COLUMN reviewed_by varchar(64);
ALTER TABLE fp_checks ADD COLUMN reviewed_at timestamptz;
CREATE INDEX fp_checks_review ON fp_checks(review_status, captured_at DESC, id);

-- 테스트 영상은 기준·외부 데이터셋 이미지로 만든 것이라 검토·데이터셋 대상이 아니다. V33이 등록한 행을 지우고,
-- 이후에는 설정(app.review.exclude-cameras, 기본 test-video*)에 맞는 카메라를 등록하지 않는다.
DELETE FROM fp_checks WHERE camera_id LIKE 'test-video%';

-- V33 방식(워커가 직접 데이터셋에 넣음)의 기록은 사람 확인을 거치지 않았으므로 데이터셋 표시를 지우고 검토 대기로 돌린다.
UPDATE fp_checks SET dataset_name = NULL, dataset_split = NULL, dataset_boxes = NULL, skip_reason = NULL,
  review_status = CASE WHEN coalesce(false_count, 0) + coalesce(unsure_count, 0) > 0 THEN 'PENDING' ELSE 'NOT_NEEDED' END
WHERE status = 'DONE';
DELETE FROM external_datasets WHERE url = 'local:gemma-fp';
