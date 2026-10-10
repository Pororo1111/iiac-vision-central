# 테스트 영상 CLI 현황과 과거 검증

기준일: 2026-09-26. 현재 서비스 구조는 [통합 아키텍처](ARCHITECTURE.md), 활성 자동 처리는 [AI 파이프라인](AI_PIPELINE.md)을 따른다.

## 현재 실행 경로

관리자 화면의 데이터셋 구성에서 LABELED 이미지를 최대 40장 선택한다. Python 워커가 기존 YOLO 박스를 생성하고 Gemma 클래스와 일치하며 모든 검출 박스의 신뢰도가 0.6 이상인 이미지를 선별한다. YOLO 검출 하한은 0.25이며, 그보다 낮은 미검출 객체까지 검증하는 것은 아니다. 두 모델의 일치는 정답 보증이 아니므로 `humanReviewed=false`를 보존한다. 빈 태그·빈 검출·클래스 불일치·저신뢰·잘못된 좌표는 이미지 전체를 제외한다.

선별된 이미지는 기존 기준 데이터와 복사해 독립 스냅샷을 만든다. 기준 데이터의 동일 테스트 영상 출처는 train·val·test에서 제외하고, 평가도 이 스냅샷의 test를 사용한다. 모델 학습 화면에서 epoch를 지정해 후보를 만들고 평가한다. 기준 mAP50–95 이상인 후보만 배포 후보로 등록할 수 있다. 기준과 같은 점수는 성능 향상을 뜻하지 않는다.

CLI는 다음과 같다. 실제 웹캠 대신 이미 로컬에 있는 테스트 영상을 사용하며, `collect`는 기본 loopback 중앙으로만 전송한다.

```sh
PY=.venv/bin/python
RUN=.artifacts/training-runs/새로운-실행명
$PY scripts/train-test-video.py collect --run "$RUN" --frames 4
$PY scripts/train-test-video.py analyze --run "$RUN"
$PY scripts/train-test-video.py label --run "$RUN" --device mps
$PY scripts/train-test-video.py train --run "$RUN" --epochs 1 --baseline-limit 8 --device mps
$PY scripts/train-test-video.py evaluate --run "$RUN" --device mps
$PY scripts/train-test-video.py cleanup --run "$RUN"
```

`--baseline-limit`은 CLI 축소 검증용이며 관리자 화면은 기준 train 전체를 사용한다. `selection.json`의 이미지 해시를 검사하고 이미 만든 데이터셋은 선별 입력과 제한 값이 같을 때만 재사용한다. `cleanup`은 검증 장치의 중앙 이미지·작업을 정리한다. 로컬 산출물은 별도 정리해야 한다. 학습 잠금은 CLI와 큐에서 공유한다.

워커가 종료되면 임대 만료 후 실패 처리한다. 자동 재학습·자동 현장 교체는 하지 않는다. 데이터셋 실패는 새 실행으로 재구성한다. 사용자 요청에 따라 감시 일시중지를 유지하며 테스트 시 필요한 소량만 수집한다.

## 6클래스 학습 (2026-10-09)

- 클래스 표는 `scripts/ai_assets.py`의 `CLASSES` 한곳에 둔다: `person, safety_vest, traffic_cone, hard_hat, signal_tower, smart_cone`. 새 클래스는 항상 뒤에 붙여 번호를 유지하며, 앞부분이 같은 4클래스 기준 모델·기준 데이터셋·이전 스냅샷·외부 데이터셋은 그대로 쓴다(`compatible`).
- 4클래스 베이스에서 학습하면 `expand_classes`가 분류 출력층(`cv3`·`one2one_cv3` 마지막 층)의 기존 4개 행을 옮긴 6클래스 체크포인트(`base-expanded.pt`)를 만들어 이어학습한다. YOLO26 상자 출력(`cv2`)은 채널 수가 4로 같아 이름으로 구분한다. 기준 test에서 기존 모델 mAP50–95 0.5026, 확장 직후 0.5004로 기존 클래스 판단이 유지됨을 확인했다.
- 배포 판정은 그대로 기준 test(앞 4개 클래스만 라벨) mAP50–95 비교다. 새 클래스는 기준 자료에 없으므로 `evaluation.json`의 `newClasses`에 val(보강 데이터) 클래스별 mAP를 참고값으로만 남긴다.
- 새 클래스 데이터는 `local:claude-site-devices-20261009`(id 74fcd17c, train 39·val 10)뿐이다. 외부(로보플로우) 데이터셋에는 두 클래스가 없고, 빠진 클래스 채우기도 4클래스 기준 모델로 하므로 그 사진에 장치가 찍혀 있으면 배경으로 배운다.
- 새 클래스 오버샘플링(2026-10-09): 검토한 `local:` 데이터셋 train 이미지 중 새 클래스(번호 4 이상) 박스가 있는 사진을 `NEW_CLASS_REPEAT`(10)배로 복사해 넣는다(`_r1`~`_r9` 이름, val은 복사 안 함, 스냅샷 `counts.newClassRepeated`).
- 후보의 클래스 표는 `model.json` `classes` → V36 `model_releases.classes` → 배포 정보 `classes`로 전달된다. IoT가 6클래스 모델을 받아들이는 변경은 `docs/IOT_SESSION_HANDOFF.md`에 인계했고 미구현이다.

## 이전 파이프라인 기록

### 이전 단계

| 단계 | 실행 위치와 결과 |
|---|---|
| 수집 | IoT 저장소의 `scripts/send-test-video.py`가 영상 프레임을 JPEG로 추출해 장치 Bearer 인증으로 중앙 API에 전송한다. 재시도할 때 같은 파일·촬영 시각을 유지한다. |
| Gemma4 | 중앙의 기존 AI 작업 큐에 이미지별 분석을 넣는다. `gemma4:12b`가 네 클래스의 존재 여부와 가림·잘림 등 검수 사항을 설명한다. 결과와 모델 digest를 작업 디렉터리에 보존한다. |
| SAM3 | 중앙에서 다시 조회한 정규화 JPEG에 SAM3 텍스트 프롬프트를 적용해 YOLO 박스 라벨을 만든다. Gemma4와 SAM3의 클래스 판단 차이를 `review.json`에 표시한다. |
| 검토 | `label-previews/`, `review.json`을 보고 사용할 이미지와 박스를 결정한다. 기본값은 모두 제외다. 오라벨은 제거하거나 좌표를 수정한다. |
| 데이터셋 | 검토한 테스트 영상 프레임을 train에 넣고, 기존 기준 데이터셋의 동일 영상 출처 `Wle4VAjPWVU`를 val/test에서 제외한 독립 스냅샷을 만든다. 원본 데이터는 변경하지 않는다. |
| 학습 | 기존 YOLO 가중치로 이어학습한다. 결과는 작업 디렉터리의 후보 모델이며 IoT 활성 모델을 바꾸지 않는다. |
| 선택 평가 | 독립 기준 test 분할에서 기존 모델과 후보를 같은 조건으로 비교한다. 성능이 낮은 후보는 승격 대상으로 표시하지 않는다. |

### 이전 실행 절차 (현재 사용 중단)

Docker와 Colima를 시작하지 않는다. 먼저 `python3 scripts/dev.py start`로 로컬 PostgreSQL·백엔드·프론트엔드·AI 워커를 실행한다. Ollama에 `gemma4:12b`와 `../sam3-yolo-pipeline/models/sam3.pt`가 있어야 한다.

`/ai-training`은 단일 캔버스에서 중앙 수집·Gemma4 작업과 가장 최근 로컬 실행의 단계를 5초마다 갱신한다. 아래 CLI는 `.artifacts/training-runs/<실행명>/progress.json`에 진행 상태를 기록한다. 작업 중에는 5초마다 갱신 시각을 기록하고, 20초 이상 신호가 없으면 화면에 상태 확인이 필요하다고 표시한다. 기존 실행 중 이 기록이 없는 작업은 화면에서 로컬 진행 상태를 확인할 수 없다.

```sh
cd central-server
RUN=.artifacts/training-runs/video-$(date +%Y%m%d-%H%M%S)
python3 scripts/train-test-video.py collect --run "$RUN" --frames 16
python3 scripts/train-test-video.py analyze --run "$RUN"
.venv/bin/python scripts/train-test-video.py label --run "$RUN"
```

`collect`는 로컬 PostgreSQL에 `verify-video-*` 전용 장치를 생성한다. 토큰은 Git 제외 작업 디렉터리에 권한 600으로 저장한다. IoT 전송 스크립트는 토큰을 환경 변수로만 받는다. 중앙 API에서 각 이미지를 다시 받아 SHA-256을 확인하고 그 파일을 라벨링한다.

`review.json`의 각 `items`에 `include: true`를 지정하고 `labels`를 YOLO 형식(`class_id x_center y_center width height`, 0~1 좌표)으로 검토·수정한다. 전체를 확인한 뒤 최상위 `reviewed: true`, `reviewer`를 기록한다. `disagreement`가 true인 프레임을 우선 살핀다. Gemma4의 설명은 박스 좌표의 정답으로 사용하지 않는다.

```sh
.venv/bin/python scripts/train-test-video.py dataset --run "$RUN"
.venv/bin/python scripts/train-test-video.py train --run "$RUN" --epochs 5 --device mps
.venv/bin/python scripts/train-test-video.py evaluate --run "$RUN" --device mps
```

`--baseline-limit`은 빠른 연결 검증에서 기준 train 이미지를 일부만 사용할 때 지정한다. 최종 학습에는 사용하지 않는다. `train`은 실행이 끝날 때까지 해당 명령에서 로그를 출력한다. 학습이 끝나면 `model.json`에 가중치 경로와 해시를 기록한다. 평가는 기준 모델과 후보를 같은 test 분할에서 측정한다. 같은 출처의 영상이나 자동 생성 라벨만으로 실제 현장 성능을 주장하지 않는다.

```sh
python3 scripts/train-test-video.py cleanup --run "$RUN"
```

`cleanup`은 중앙 DB와 미디어 저장소에서 `verify-video-*` 장치·샘플만 지운다. 작업 디렉터리의 데이터셋·평가·후보 모델은 보존한다. 이 자료에는 테스트 영상 프레임이 있으므로 Git에 넣지 않는다.

### 전환 전 검증 범위

2026-09-26에 영상 16장을 IoT 전송 API로 수집하고 재전송·중앙 재조회·해시 확인을 수행했다. Gemma4 분석 16건, SAM3 라벨 생성 30개 후보 박스를 확인했으며 두 결과가 다른 4장을 검토 목록에 표시했다. 사람이 검토한 7장을 사용한 전체 학습 스냅샷은 train 2,634장, val 114장, test 82장이었고, 동일 영상 출처 10장을 검증 분할에서 제외했다. 학습 시작 경로는 기준 train을 256장으로 제한한 1 epoch 연결 검증으로 확인했다. 그 후보의 독립 test mAP50-95는 기준 0.503보다 낮은 0.176이어서 현장 모델로 적용하지 않았다. 이는 축소 학습의 연결 검증 결과이며 품질 목표가 아니다. 전체 학습 완료는 이번 검증 범위가 아니다.
