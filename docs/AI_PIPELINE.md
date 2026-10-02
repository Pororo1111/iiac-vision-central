# 현재 AI 데이터 처리

기준일: 2026-09-27. 전체 연결·인증·저장 구조는 [통합 아키텍처](ARCHITECTURE.md)를 기준으로 한다. 이 문서는 Gemma 자동 처리·YOLO 데이터셋 구성과 화면 사용법을 설명한다.

## 자동 처리

1. IoT 이미지 수신 시 `training_samples`와 `AUTO_LABEL` 작업을 같은 DB 트랜잭션으로 등록한다. 동일 이미지 재전송은 기존 샘플을 반환한다.
2. 호스트 Python 워커가 작업을 임대하고 Ollama `gemma4:12b`에 이미지를 전달한다.
3. 이미지 설명·태그·모델 digest를 작업별로 보존하고 `LABELED`로 자동 완료한다.
4. 전체 픽셀의 98% 이상이 밝기 20/255 미만이면 객체 추론을 생략하고 판단 불가·빈 태그를 저장한다. 일반 저조도 오분류율은 측정하지 않았다.

임대는 120초, heartbeat는 20초다. 만료 lease의 결과는 거부한다. 실패한 NEW 이미지는 진행 중 작업이 없을 때 재시도 API로 다시 등록할 수 있다.

이 자동 라벨링 단계는 SAM3를 로드하지 않으며 박스를 생성하지 않는다. 후속 데이터셋 구성 단계에서 기존 YOLO가 박스 초안을 만든다. 사람 승인 없이 완료하고 과거 `APPROVED`·검토 이력은 별도로 보존한다. V7은 자동 작업 등록, V8은 Gemma 전용 완료 상태와 이전 작업 전환을 담당한다.

## 화면 사용

중앙 AI 학습(`/ai-training`)에서 아래 다섯 단계로 이동한다.

| 단계 | 경로 | 동작 |
|---|---|---|
| 이미지 수집 | `/ai-training/collect` | 수신 이미지·집계·필터·페이지·가로 넘김 |
| 자동 라벨링 | `/ai-training/label` | 자동 설명·태그·처리 현황 확인 |
| 데이터셋 구성 | `/ai-training/dataset` | LABELED 이미지 선택·YOLO 박스·Gemma 태그 선별·스냅샷 구성 |
| 모델 학습 | `/ai-training/train` | 데이터셋별 학습·평가 요청, 결과·후보 등록 |
| 모델 배포 | `/ai-training/deploy` | 모델·장치 선택, 배포 요청·상태 조회. IoT 적용은 명시적 실행 필요 |

`/training-data`도 수집 화면을 제공한다. 예전 `analyze`·`review` 경로는 `label`로 이동한다. 직접 업로드·기준 유니폼·수동 프롬프트·수동 검토·박스 오버레이 UI는 제거했다. 데이터셋 구성 단계에서만 학습할 이미지를 선택한다. YOLO 검출과 Gemma 태그가 일치하는 자료를 의사 라벨로 선별하며 사람 검증 정답으로 표시하지 않는다. 상세 정책은 [학습 가이드](TRAINING_PIPELINE.md)를 따른다.

## 실행과 저장

로컬 실행 명령과 설치는 [README](../README.md#로컬-개발-실행-docker-사용-안-함)를 따른다. Docker·Colima를 시작하지 않는다.

- `dev.py`는 프로젝트 `.venv/bin/python`을 워커 런타임으로 사용한다. 필요하면 `AI_PYTHON`을 지정한다. Pillow와 호스트 Ollama 모델이 필요하다.
- Next.js 8200 → Spring Boot 8201 → PostgreSQL 55432 구조다. 워커는 loopback의 `/internal/ai/**`와 Ollama 11434에 접근한다.
- DB·이미지·로그는 `.artifacts/local-dev/`, 기존 CLI 산출물은 `.artifacts/training-runs/`에 보존하며 Git에서 제외한다.
- `scripts/register-baseline.py`의 기준 데이터 정보는 `.artifacts/baseline.json`에 저장한다. 기준 train 2,627장·val 124장·test 82장을 IoT 수집 통계에 합산하지 않는다.
- 중지된 Docker 볼륨은 별개다. V5 정리 대상의 물리 삭제 완료를 주장하지 않는다.

## API·검증

현재 API는 [API 계약](API.md#이미지-수집자동-라벨링)을 따른다. 관리자 세션+CSRF, IoT Bearer, 워커 Bearer를 구분한다. 과거 검토 API는 코드에 남아 있지만 현재 화면의 자동 완료 과정에는 사용하지 않는다.

테스트는 전용 `verify-*` 장치를 만들고 해당 데이터만 정리한다. 전역 claim 검증은 AI 워커·실제 대기열과 충돌하지 않도록 해야 한다. 자동 라벨링 테스트는 검증 장치 작업만 검사한다. 과거 실행 결과는 [검증 기록](VERIFICATION.md), 기존 CLI의 제한은 [CLI 현황](TRAINING_PIPELINE.md)에 기록했다.
