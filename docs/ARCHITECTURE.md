# 중앙 서버 통합 아키텍처

기준일: 2026-09-27. 현재 작업트리의 코드·설정을 기준으로 정리했다. 이 문서는 **현재 구조의 기준 문서**이며, 실행 절차는 [README](../README.md), HTTP 계약은 [API](API.md), 과거 실행 결과는 [검증 기록](VERIFICATION.md)을 따른다. 중앙 로컬 서비스와 학습 흐름을 검증했다. IoT의 실제 모델 교체·서비스 재시작은 이번 검증에 포함하지 않는다.

사용자 요청으로 추가 구현을 중단했다. 남은 작업·완료 조건·재개 순서는 [개발 현황](DEVELOPMENT_PLAN.md)에 정리했다. IoT 배포 CLI·실제 중앙 재전송 계약은 검증했으며, 워커 결과 영구 보관은 미완성 초안이고 수집 화면 브라우저 검증은 남아 있다.

## 1. 접속 주소

중앙 서버 접속 URL은 **MagicDNS**, IoT 서버 접속 URL은 현재 **Tailscale IP**로 안내한다. 같은 tailnet 연결과 접근 정책 허용이 필요하다.

| 대상 | 주소 | 용도 |
|---|---|---|
| 중앙 관제 | 중앙 서버 `https://<central-host>:9443` | 관리자 로그인·장치·이벤트·실시간 카메라 |
| 중앙 AI 학습 | AI 학습(`/ai-training`) | 수집·자동 라벨링과 후속 단계 현황 |
| 중앙 영상 연결 | WHEP 진입점 `https://<central-host>:9444` | 앱이 발급받은 경로·토큰으로 연결; 일반 관제 페이지가 아님 |
| IoT 현장 화면 | IoT 서버 `http://<iot-ip>:8100` | 현장 서버 화면 |
| 중앙 호스트 내부 개발 | `http://localhost:8200` | 로컬 브라우저 개발 접속 |

중앙의 `<central-ip>`는 RTMP 바인딩과 WebRTC ICE 설정에 쓰이는 네트워크 주소다. 사용자에게 안내하는 중앙 웹 URL은 위 MagicDNS 주소를 사용한다.

## 2. 프로젝트 경계와 구성 요소

| 구성 요소 | 책임 | 주요 위치 |
|---|---|---|
| 현장 IoT 서버 | 카메라 추론·침입 판별·장치 제어, 중앙 등록·heartbeat·이벤트·이미지 전송, 오버레이 영상 송출 | 독립 프로젝트 `../iot-server`; 실제 원격 `~/iot-server` |
| Next.js 16.3.5 / React 19.3.0 | 로그인·통합 관제·AI 학습 화면, 로컬 `/api/**` 프록시 | `frontend/` |
| Java 21 / Spring Boot 4.0.8 | 인증·입력 검증·업무 규칙·작업 큐·영상 접근 권한 | `backend/` |
| MyBatis 4.0.1 / PostgreSQL 17 | SQL 매핑, 메타데이터·이벤트·AI 결과·학습·배포 저장 | Mapper와 Flyway V1~V10 |
| 호스트 Python 워커 | 작업 임대·이미지 조회·Ollama 호출·결과 반환 | `ai-worker/worker.py` |
| Ollama / `gemma4:12b` | 이미지 설명·분류 태그 생성 | 호스트 `127.0.0.1:11434` |
| MediaMTX 1.21.1 | IoT RTMP 수신·브라우저 WebRTC 전달 | `media/mediamtx.yml` |
| Tailscale Serve | 사설망 HTTPS 진입·TLS 종료 | 중앙 9443·9444 |
| Nginx / Docker Compose | 별도의 운영 배포 구성 | `nginx/`, `compose*.yaml` |
| AI 자산 | 기준 모델·사전학습 모델·기준 데이터셋·로보플로우 데이터셋 | Git 제외 `.artifacts/ai-assets/`, 경로 `scripts/ai_assets.py` |

Spring Boot는 추론·학습을 실행하지 않는다. 자동 라벨 작업은 Gemma를 사용하고, 데이터셋 구성·학습·평가 작업은 별도 Python 자식 프로세스에서 기존 YOLO를 사용한다. 인접 학습 프로젝트의 Python 환경을 재사용해도 SAM3 실행을 뜻하지 않는다.

## 3. 현재 로컬 개발 연결

```text
관리자 브라우저 / IoT의 중앙 API 요청
  → Tailscale Serve HTTPS 9443
  → Next.js 127.0.0.1:8200
      ├─ 화면 제공
      └─ /api/** → Spring Boot 127.0.0.1:8201
                       ├─ MyBatis → PostgreSQL 127.0.0.1:55432
                       └─ 수집 JPEG → .artifacts/local-dev/media/

호스트 AI 워커
  ├─ /internal/ai/** → Spring Boot 127.0.0.1:8201
  └─ 이미지 추론 → Ollama 127.0.0.1:11434
```

- `scripts/dev.py start|stop|status`로 로컬 프로세스를 관리한다. Ollama와 모델은 별도 준비가 필요하다.
- Docker·Colima·Nginx는 현재 로컬 개발의 실행 경로에 포함되지 않는다.
- Next.js는 `DEV_API_ORIGIN`이 설정된 경우 `/api/**`만 전달한다. `/internal/**`는 외부에 프록시하지 않는다.
- DB·백엔드·Next.js·WHEP HTTP·MediaMTX 관리 API는 loopback에 바인딩한다.
- 기존 `scripts/expose-tailscale.py`는 Docker Compose를 시작하는 운영 구성용 스크립트다. 현재 로컬 개발에서는 실행하지 않는다.

별도 운영 구성은 Nginx가 `/`를 Next.js로, `/api/`를 Spring Boot로 분배한다. 일반 도메인·인증서 기반 HTTPS는 구성만 준비되어 있으며 실제 배포 검증과 구분한다.

## 4. 데이터 흐름

### 관제·이벤트

1. 사전 발급한 장치 토큰으로 IoT가 등록하고 5초마다 heartbeat를 전송한다.
2. 중앙은 마지막 수신 후 30초가 넘으면 오프라인으로 계산한다. 감지 실행 상태는 별도 필드다.
3. 침입 시작·종료 이벤트는 IoT가 생성한 UUID와 발생 시각을 전송한다. 중앙은 수신 시각도 저장한다.
4. 이벤트 UUID 고유 제약과 `ON CONFLICT`로 재전송 중복을 막는다. 장치·기간·유형 필터와 안정적인 정렬·페이지 조회를 제공한다.
5. 관리자 화면은 장치·이벤트를 5초 주기로 갱신한다.

모의 IoT는 SQLite outbox를 사용한다. 실제 IoT의 상세 구현 이력은 [연동 문서](IOT_CENTRAL_HANDOFF.md)와 [이전 점검](IOT_CURRENT_IMPLEMENTATION.md)에 남아 있으며, 원격의 현재 동작은 별도 확인해야 한다.

### 이미지 수집·자동 처리

1. IoT가 `POST /api/iot/training-samples`로 원본 이미지와 장치·카메라·촬영 시각을 전송한다.
2. 중앙은 이미지를 검증·JPEG 정규화하고 파일로 저장한다. DB의 이미지 레코드와 `AUTO_LABEL` 작업 등록은 같은 트랜잭션이다. 파일시스템 자체가 DB 트랜잭션에 포함되지는 않으며 DB 저장 실패 시 생성 파일을 제거한다.
3. 장치·카메라·촬영 시각·정규화 이미지 해시로 중복 수신을 식별한다. 중복 수신은 기존 샘플을 반환한다.
4. 워커가 작업을 임대하고 이미지를 받아 Gemma 설명·태그를 생성한다. 임대 시간은 120초, 워커 heartbeat는 20초다.
5. 완료된 작업 결과와 모델 digest를 보존하고 이미지를 `LABELED`로 변경한다. 과거 검토 이력은 유지한다.
6. 전체 픽셀의 98% 이상이 밝기 20/255 미만이면 객체 추론을 생략하고 판단 불가·빈 태그를 저장한다.

관리자 수동 입력(2026-09-27): 이미지는 Spring이 같은 검증·정규화로 바로 저장한다. 영상 파일·유튜브 링크는 `media_imports` 작업으로 등록하고 호스트 AI 워커(`ai-worker/importer.py`)가 OpenCV로 지정 간격 프레임을 추출해 내부 API로 전송한다. 유튜브는 venv의 yt-dlp로 음성 없는 720p 이하 영상 스트림 하나만 받는다(ffmpeg 불필요, 1시간·500MB 이하, 생방송·재생목록 거부). 거의 같은 연속 프레임은 저장하지 않는다. 저장된 프레임은 1~6단계를 그대로 거친다. 수동 입력이 없으면 IoT 수집과 주기 트리거만으로 진행한다.

현재 자동 라벨은 **이미지 설명과 분류 태그**다. SAM3·박스 생성·수동 프롬프트·수동 검토 UI는 제거했다. 관리자가 데이터셋 구성을 요청하면 기존 YOLO로 박스 초안을 만들고 Gemma 태그와 클래스가 일치하며 신뢰도 기준을 통과한 이미지만 선별한다. SAM3와 수동 검토 UI는 사용하지 않는다. 자동 생성 라벨은 사람 검증 정답으로 표시하지 않는다.

### 실시간 영상

```text
IoT 오버레이 H.264
  → 중앙 RTMP <central-ip>:1935
  → MediaMTX
      ├─ WHEP: MagicDNS HTTPS 9444 → 127.0.0.1:8889
      └─ WebRTC ICE: <central-ip>:8189 TCP/UDP → 관리자 브라우저
```

- 관리자 화면의 카메라 보기에서 세션+CSRF로 5분짜리 난수 read 토큰을 발급받는다.
- MediaMTX는 내부 `/internal/media/auth`로 publish/read 권한을 확인한다. publish는 장치 토큰과 `devices/{deviceId}/{cameraId}` 경로를, read는 토큰·만료·정확한 경로를 검증한다.
- Spring Boot는 영상 바이트를 중계하지 않는다. read 토큰은 서버 메모리에 보관한다.
- MediaMTX 관리 API는 `127.0.0.1:9997`, metrics는 `127.0.0.1:9998`이다. 녹화는 꺼져 있다.
- 실제 `iot-jetson-001 / usb-0`의 640×480 브라우저 재생 검증 기록이 있다. 이번 문서 정리에서 재생을 다시 검증한 것은 아니다.

## 5. 인증 경계

| 호출자 | 인증·보호 |
|---|---|
| 관리자 `/api/**` | Spring Security 서버 메모리 세션, HttpOnly·SameSite=Lax 쿠키, 상태 변경 CSRF, 미인증 401 JSON |
| IoT `/api/iot/**` | 장치별 Bearer, DB에는 SHA-256 해시, 인증 장치 ID와 요청 ID 일치 검사, 무상태·CSRF 예외 |
| 워커 `/internal/ai/**` | 별도 워커 Bearer, 무상태·CSRF 예외, loopback 접근 |
| MediaMTX `/internal/media/auth` | 내부 콜백에서 publish/read 자격 증명 검증, loopback 접근 |

관리자 비밀번호는 BCrypt다. 백엔드 재시작 시 관리자 세션과 영상 read 토큰은 만료된다. 초기 seed는 기존 자격 증명을 덮어쓰지 않는다.

현재 `dev.py`는 HTTP 개발을 위해 실행 환경의 `COOKIE_SECURE=false`를 강제한다. Tailscale HTTPS로 접속해도 이 개발 쿠키 설정은 그대로다. 운영 구성은 `COOKIE_SECURE=true`를 사용해야 한다. 과거 Secure 쿠키 검증 기록은 현재 개발 실행 설정과 구분한다.

## 6. 저장 구조

| 저장소 | 내용 |
|---|---|
| `admins`, `devices`, `events` | 관리자·장치·토큰 해시·heartbeat·침입 이벤트 |
| `device_cameras` | 카메라 송출 상태·해상도·FPS |
| `training_samples` | 이미지 해시·출처·시각·상태·최신 결과 |
| `labeling_jobs` | 작업 종류·상태·lease·모델·결과·오류 |
| `annotation_reviews` | 과거 수동 검토 이력; 현재 자동 완료와 구분 |
| `training_runs`, `training_tasks` | 데이터셋 스냅샷·학습 단계·워커 작업 임대 |
| `model_releases`, `model_deployments` | 검증 후보와 장치별 배포 이력 |
| `pending_media_deletions` | 정리할 이미지 파일 목록 |
| `.artifacts/local-dev/` | 로컬 PostgreSQL 데이터·JPEG·프로세스 로그 |
| `.artifacts/training-runs/` | 별도 CLI의 진행 상태·데이터셋·평가·후보 가중치 |
| `.artifacts/ai-assets/` | 기준·사전학습 모델, 기준 데이터셋, 로보플로우 다운로드 |
| `.venv/` | AI 워커·학습·영상 분석 Python 환경 |
| `.artifacts/baseline.json` | 기존 데이터셋·모델 기준 정보; IoT 수집 통계에 합산하지 않음 |

Flyway 적용 이력은 보존한다. UTC ISO 8601과 PostgreSQL `timestamptz`를 사용한다. `.env`, 토큰, DB, 이미지·모델 산출물은 Git에 넣지 않는다. 중지된 Docker 볼륨은 현재 로컬 DB와 별개이며 삭제 완료로 간주하지 않는다.

## 7. 화면과 구현 범위

| 경로·단계 | 현재 구현 |
|---|---|
| `/` | 통합 관제·장치·이벤트·카메라 보기 |
| `/training-data`, `/ai-training/collect` | 수신 이미지 집계·필터·페이지·가로 넘김, 이미지·영상 파일·유튜브 링크 직접 올리기와 영상 가져오기 상태 |
| `/ai-training/label` | Gemma 자동 처리 결과·태그·작업 현황 |
| `/ai-training/dataset` | LABELED 이미지 최대 40장 선택 → YOLO·Gemma 자동 선별 → 기준 자료와 독립 스냅샷 구성 |
| `/ai-training/train` | 데이터셋별 1~100 epoch 학습 요청, 상태·후보 해시, 기준 모델과 평가, 통과 후보 등록 |
| `/ai-training/deploy` | 등록 후보·대상 장치 선택, 배포 요청·상태 이력. 현장 자동 적용은 별도 IoT의 명시적 실행 필요 |

`/ai-training`은 다섯 단계 메뉴다. 과거 `analyze`·`review` 링크는 `label`로 이동한다. 관제·학습 화면은 밝은 배경·흰색 카드·청록 강조를 사용한다.

별도 CLI의 `label` 명령은 기존 YOLO·Gemma 자동 선별로 변경했다. `selection.json`은 정책·모델 해시·이미지 해시·포함 여부·사유·박스를 보존한다. 기준 데이터는 복사해 스냅샷을 만들며 테스트 영상 출처 `Wle4VAjPWVU`는 학습·검증·평가 분할에서 제외한다. [실행 가이드](TRAINING_PIPELINE.md)를 따른다.

### 데이터셋·학습 작업 큐

- `training_runs`는 선택 이미지·당시 Gemma 결과의 스냅샷, 현재 단계와 요약을 저장한다.
- `training_tasks`는 PREPARE/TRAIN/EVALUATE 작업과 임대를 저장한다. 활성 작업은 전체에서 하나만 허용한다.
- 관리자 API는 세션·CSRF로 요청을 등록한다. `/internal/ai/training/**`는 기존 워커 인증을 사용한다.
- 워커는 20초마다 120초 임대를 갱신한다. 임대 상실 시 학습 자식 프로세스를 중단한다. 만료 작업은 FAILED로 기록하며 자동 재실행하지 않는다. 새 실행을 생성해 다시 구성한다.
- CLI와 큐의 YOLO 실행은 같은 파일 잠금을 사용한다. 웹캠 수집을 자동으로 시작하지 않는다.

### 모델 배포

- 평가한 후보는 `release/model.pt`에 복사한다. 관리자 등록 시 평가 통과와 SHA-256·최대 512 MiB를 확인해 `model_releases`에 저장한다.
- `model_deployments`는 장치별 활성 요청을 하나만 허용한다. IoT는 기존 장치 Bearer로 자기 배포 명세와 모델만 요청할 수 있다.
- 상태는 PENDING → DOWNLOADING → VALIDATING → APPLYING → APPLIED다. 비완료 단계에서 FAILED, APPLYING에서 ROLLED_BACK을 허용한다. 관리자는 적용 전 단계(PENDING·DOWNLOADING·VALIDATING)의 배포를 `POST /api/training/deployments/{id}/cancel`(세션+CSRF)로 CANCELED 처리할 수 있다. 이미 CANCELED면 그대로 성공, APPLYING 이후·종료 상태는 409 `DEPLOYMENT_NOT_CANCELABLE`, 없는 id는 404다. 취소 후 IoT의 desired·파일·상태 보고는 종료 배포와 같이 빠지거나 409로 막힌다(V19). 완료에는 대상 모델 해시가 필요하다.
- 같은 상태·같은 활성 해시의 보고는 재전송 성공 처리한다. 종료된 요청은 다시 다운로드할 수 없다.
- 중앙이 현장 서비스를 재시작하거나 웹캠을 켜지 않는다. 실제 현장 적용 검증은 아직 하지 않았다.
- 원격 IoT의 명시적 CLI와 중앙 계약 검증 코드는 `c137838`에 있다. 실제 중앙 HTTPS의 가짜 후보 다운로드·해시·FAILED 보고·종료 다운로드 차단을 확인했다. 실제 모델 GPU 검증·적용·복구와 실행 서비스의 새 코드 반영은 별도 작업이다.


## 8. 검증과 문서 관리

- 과거 실제 PostgreSQL 인증·중복 저장·Gemma 자동 완료·WebRTC 재생 검증 결과는 [검증 기록](VERIFICATION.md)에 보존한다.
- 검증은 `verify-*` 장치를 사용하고 종료 시 해당 데이터·이미지를 정리한다. 공유 AI 대기열을 임의 소비하지 않는다.
- `tests/recovery.py`는 Docker 중단·복구 전용이므로 현재 로컬 개발에서 실행하지 않는다.
- 구조 변경 시 이 문서를 먼저 갱신하고 API·실행 문서를 맞춘다. 과거 검증 기록을 현재 구현 설명으로 재사용하지 않는다.
