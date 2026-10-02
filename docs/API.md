# API 계약 v1

공개 API는 `/api` 하위입니다. 현재 로컬 개발은 Next.js rewrite, 별도 운영 구성은 Nginx를 통해 Spring Boot로 전달합니다. 전체 구조는 [통합 아키텍처](ARCHITECTURE.md)를 따릅니다. 별도 표기가 없으면 JSON 요청·응답입니다.
시각은 UTC ISO 8601(`2026-09-22T01:00:00Z`)로 교환합니다. DB는 timestamptz를 사용합니다.

## 관리자 인증

- `GET /api/auth/csrf`: 익명 접근 가능. `{ "headerName": "X-CSRF-TOKEN", "token": "..." }` 반환. 세션 쿠키도 함께 유지합니다.
- `POST /api/auth/login`: **application/x-www-form-urlencoded**의 `username`, `password` 및 CSRF 응답의 헤더를 전송. 성공 200 `{ "authenticated": true }`, 실패 401. 성공 시 세션 ID가 교체됩니다.
- `GET /api/auth/me`: 200 `{ "username": "admin" }`, 미인증 401.
- `POST /api/auth/logout`: 현재 세션의 CSRF 필요, 성공 204. 로그인 뒤 CSRF 토큰을 다시 조회하세요.

Nginx 운영 구성에서 로그인 속도를 제한합니다. 현재 로컬 Next.js 경로에는 이 Nginx 제한이 적용되지 않습니다. 초과 시 429이며 프론트엔드에서 재시도 안내를 표시합니다.

## IoT 공통

`Authorization: Bearer <장치별 토큰>`이 필요합니다. 토큰은 중앙에서 사전 발급·등록합니다. 장치 ID는 토큰 소유자와 같아야 합니다. 관리자 쿠키로 대체할 수 없습니다. CSRF는 필요 없습니다.

### POST /api/iot/register

```json
{"deviceId":"iot-001","name":"현장 1"}
```

장치 ID는 영문·숫자·밑줄·하이픈 1~64자, 이름은 공백만으로 구성할 수 없으며 최대 120자입니다. 성공 200 `{"registered":true}`. 반복 호출은 동일 장치 정보를 갱신합니다. 사전 발급하지 않은 ID를 생성하는 API가 아닙니다.

### POST /api/iot/heartbeat

```json
{"deviceId":"iot-001","modelName":"best.pt","runtimeStatus":"RUNNING"}
```

모델명은 최대 200자(정보가 없으면 빈 문자열). 상태는 `RUNNING`, `STOPPED`, `STALE`, `ERROR`, `UNKNOWN`. 요청에 선택 필드 `activeSha256`(현재 활성 모델 SHA-256, 소문자 64자리)을 보낼 수 있으며 생략하면 이전 값을 유지한다. 성공 200 `{"accepted":true,"monitoring":null,"deployment":null}`. 중앙 관리자가 감시 중지·재개를 요청했으면 `monitoring`은 `{"commandId":"UUID","action":"PAUSE|RESUME","requestedAt":"UTC"}`이며 같은 명령은 반영 뒤에도 같은 commandId로 계속 전달된다. IoT는 마지막으로 적용한 commandId를 저장하고 처음 보는 commandId만 적용한 뒤, 감시 상태를 `runtimeStatus`(감시 중 `RUNNING`, 중지 `STOPPED`)로 보고한다(V18). 먼저 등록해야 하며 5초마다 호출합니다(2026-09-28 10초에서 변경). 마지막 수신 시각은 중앙 서버 시각입니다.

### 장치 진단: 명령 반영 확인·재시작·로그 (V22, 2026-09-28)

heartbeat 요청에 선택 필드를 더 보낼 수 있다. 보내지 않으면 기존과 같다.

| 필드 | 의미 |
|---|---|
| `monitoringCommandId` | 마지막으로 적용한 감시 명령 commandId(UUID). 중앙은 현재 명령과 같으면 "반영됨"으로 표시하고, 반영됐는데 감지가 안 되면 대기 대신 오류를 보여 준다. 생략하면 이전 값을 유지한다. |
| `restartCommandId` | 마지막으로 처리한 재시작 명령 commandId(UUID). 생략하면 이전 값을 유지한다. |
| `lastError` | 현재 오류 요약(최대 500자, 예: 카메라 열기 실패). 매 heartbeat의 값으로 바뀌며 생략·null이면 오류 없음으로 본다. |
| `startedAt` | IoT API 프로세스 기동 시각(UTC). 재시작 확인에 쓴다. |

heartbeat 응답에 두 필드가 추가된다. 둘 다 요청 후 2분이 지나면 전달하지 않는다(오래된 명령이 나중에 실행되지 않도록).

- `restart`: `{"commandId":"UUID","target":"WORKER|API|ALL","requestedAt":"UTC"}` 또는 null. IoT가 `restartCommandId`로 처리를 보고하면 더 이상 전달하지 않는다. IoT는 처리한 commandId를 **재시작 전에** 디스크에 저장하고, 다음 heartbeat에서 보고한 뒤 해당 서비스를 재시작한다(API 재시작 시 저장된 값을 기동 후 보고). 대상 서비스 이름 매핑은 IoT가 정한다.
- `logRequest`: `{"requestId":"UUID","lines":20~2000,"requestedAt":"UTC"}` 또는 null. IoT는 서비스별 최근 `lines`줄을 `POST /api/iot/logs`로 올린다. 받은 뒤에는 null이다.

`POST /api/iot/logs` (장치 Bearer):

```json
{"deviceId":"iot-jetson-001","requestId":"UUID","collectedAt":"2026-09-28T07:00:00Z",
 "logs":[{"service":"iot-api","text":"...\n..."},{"service":"iot-worker","text":"..."}]}
```

- 서비스 이름은 영문·숫자·`_.@-` 1~64자, 1~6개, 이름 중복 불가. 서비스별 text 최대 262,144자. 비밀값(토큰 등)을 로그에 넣어 보내지 않는다.
- 성공 200 `{"accepted":true,"duplicate":false}`. 같은 requestId 재전송은 `duplicate:true`. 현재 요청과 다른 requestId는 409 `LOG_REQUEST_MISMATCH`. 장치별 마지막 응답만 보관한다.

관리 API(관리자 세션, 쓰기는 CSRF):

- `GET /api/devices/{id}`: 장치 한 대. 목록 항목과 같은 형식이며 `monitoringApplied`, `lastError`, `startedAt`, `restartTarget`, `restartRequestedAt`, `restartApplied`가 목록에도 포함된다. `modelDisplayName`은 활성 해시(`activeSha256`) 또는 IoT가 보고한 `modelName`(배포 모델 UUID)으로 중앙 모델 목록에서 찾은 모델 이름이며, 찾지 못하면 null이다.
- `POST /api/devices/{id}/restart` `{"target":"WORKER|API|ALL"}`: 오프라인 장치는 409 `DEVICE_OFFLINE`, 없는 장치 404.
- `POST /api/devices/{id}/logs/request` `{"lines":300}`(20~2000): 오프라인 409. 응답은 아래 GET과 같다.
- `GET /api/devices/{id}/logs`: `{"request":{requestId,requestedAt,lines}|null,"pending":bool,"logs":[{service,requestId,content,collectedAt,receivedAt}]}`.

### POST /api/iot/events

```json
{
  "eventId":"9702c936-9192-4c2a-bbc7-ff6cc3aa9456",
  "deviceId":"iot-001",
  "cameraId":"camera-01",
  "zoneId":"zone-01",
  "type":"INTRUSION_START",
  "occurredAt":"2026-09-22T01:00:00Z",
  "objectCount":1
}
```

- 카메라·영역 ID는 공백만으로 구성할 수 없으며 최대 64자입니다.
- 유형은 `INTRUSION_START` 또는 `INTRUSION_END`, 객체 수는 0~1,000,000 정수입니다.
- 발생 시각은 필수이며 서버보다 5분 초과 미래면 400입니다. 과거 미전송 이벤트는 허용합니다.
- 성공 200: `{"accepted":true,"duplicate":false,"eventId":"..."}`.
- 동일 UUID 재전송도 200이며 `duplicate:true`. **같은 UUID의 내용을 갱신하지 않습니다.** 발신자는 최초 payload를 유지해야 합니다.
- 다른 장치가 사용한 UUID이면 409. 이벤트 수신은 heartbeat를 대체하지 않습니다.

## 관리 조회

관리자 세션이 필요합니다. 응답에 토큰·비밀번호 해시가 포함되지 않습니다.

- `GET /api/devices`: 배열. 각 항목은 `deviceId`, `name`, `modelName`, `runtimeStatus`, `lastSeenAt`, `registeredAt`, `online`과 감시·진단 필드(위 장치 진단 절).
- `GET /api/devices/{deviceId}/cameras`: 카메라 ID, 표시명, 송출 상태, 해상도·FPS, 마지막 송출 시각.
- `POST /api/devices/{deviceId}/cameras/{cameraId}/view-session`: 관리자 세션+CSRF 필요. 정확한 카메라 경로만 읽을 수 있는 5분짜리 토큰과 WHEP URL 반환.
- `GET /api/events`: `{items: [...], total, page, size}`.
- 선택 필터: `deviceId`, `type`, `from`, `to` (발생 시각 기준, 양 끝 포함).
- `page`는 0부터(최대 1,000,000), `size`는 1~100(기본 20).
- 이벤트는 `occurredAt DESC, eventId DESC` 고정 정렬. 각 항목은 입력 필드와 중앙 생성 `receivedAt`을 포함합니다.
- `GET /api/health`: 익명 200 `{"status":"UP"}`. API 프로세스 생존 확인용이며 DB 실시간 진단용은 아닙니다.
- `GET /api/training/progress`: 관리자 세션 필요. 로컬 학습 작업이 설정되지 않았으면 `{"available":false}`를 반환합니다. 설정된 경우 가장 최근 실행의 이름, 단계, 상태, 처리 수, 갱신 시각, 완료 단계를 반환합니다. 작업 경로와 비밀값은 반환하지 않습니다.

### POST /api/iot/cameras/status

장치 Bearer 인증이 필요하며 본문의 `deviceId`는 인증 장치와 같아야 합니다.

```json
{"deviceId":"iot-jetson-001","cameraId":"usb-0","displayName":"현장 웹캠","streamStatus":"STREAMING","width":640,"height":480,"fps":5.0,"lastError":null}
```

`streamStatus`는 `STARTING`, `STREAMING`, `OFFLINE`, `ERROR` 중 하나입니다. 영상 publish는 `devices/{deviceId}/{cameraId}` 경로만 허용하며 중앙 장치 토큰을 MediaMTX 외부 인증에서 다시 검증합니다.

## 오류

API 오류 형식은 `{"error":"CODE"}`입니다. Nginx 자체 오류(429,502 등)는 다른 형식일 수 있습니다.

| 상태 | 코드 / 의미 |
|---|---|
| 400 | INVALID_REQUEST, INVALID_DATE_RANGE, FUTURE_EVENT |
| 401 | UNAUTHORIZED, INVALID_CREDENTIALS |
| 403 | FORBIDDEN (CSRF·권한), DEVICE_MISMATCH |
| 409 | DEVICE_NOT_REGISTERED, EVENT_ID_CONFLICT, DEVICE_OFFLINE, LOG_REQUEST_MISMATCH |

통신 장애·5xx·429는 동일 UUID로 백오프 후 재시도합니다. 인증·입력 오류는 큐를 보존하고 설정을 수정합니다.

## 이미지 수집·자동 라벨링

| API | 동작 |
|---|---|
| POST `/api/iot/training-samples` | 장치 Bearer, multipart `file`, `deviceId`, `cameraId`, `capturedAt`, `title`; JPEG/PNG 최대 10 MiB, 각 변 최소 32px·총 2천만 픽셀 이하. 촬영 시각은 서버보다 5분 초과 미래 불가. 정상·중복 모두 샘플 반환 |
| GET `/api/training/stats` | 수집 상태·작업 집계; `labeled`, `autoQueued`, `autoRunning` 포함 |
| GET `/api/training/samples` | `page`, `deviceId`, `status` 필터; `{items,total,page,size}`, 페이지당 40개 |
| GET `/api/training/samples/{id}` | 이미지 메타데이터·최신 결과 |
| GET `/api/training/samples/{id}/image` | 관리자 인증 JPEG |
| GET `/api/training/label-jobs` | `page`, 선택 `sampleId`로 작업 이력 조회 |
| POST `/api/training/samples/{id}/retry-label` | 세션+CSRF, NEW이며 대기·실행 작업이 없을 때 202; 그 외 409 |
| POST `/api/training/samples/{id}/review` | 과거 검토 API는 유지; 세션+CSRF와 `{revision,status,result}` 필요. 현재 화면에는 검토 UI 없음 |

- 이미지 레코드와 `AUTO_LABEL` 작업은 같은 DB 트랜잭션으로 등록한다. 중복 수신은 새 작업을 추가하지 않는다.
- 새 자동 작업은 Gemma 설명·태그를 저장하고 `LABELED`로 완료한다. 조회 상태는 `NEW`, `REVIEW`, `APPROVED`, `EXCLUDED`, `LABELED`다.
- 작업 `kind`의 `ANALYSIS`는 이전 수동 분석 이력, `AUTO_LABEL`은 현재 Gemma 자동 처리다.
- `result.boxes`는 null 또는 빈 배열만 허용한다. 비어 있지 않은 작업 완료 결과는 409 `BOX_LABELING_DISABLED`다.
- POST `/api/training/analysis-jobs`, `/api/training/label-jobs`, `/api/training/samples`, `/api/training/references`는 제거했다.
- 자동 완료는 사람 승인으로 기록하지 않는다. 과거 REVIEW·APPROVED와 검토 이력은 보존한다.

## 관리자 수동 입력 (2026-09-27)

사용자는 IoT 수집 외에 이미지·영상 파일·유튜브 링크를 직접 올릴 수 있다. 올린 이미지·추출 프레임은 IoT 이미지와 같은 `AUTO_LABEL` 대기열에 들어가며 이후 자동 라벨링·주기 트리거·수동 학습·배포 흐름은 같다. 모두 관리자 세션+CSRF가 필요하다.

| API | 동작 |
|---|---|
| POST `/api/training/uploads` | multipart `files` 1~20개. JPEG/PNG 장당 10 MiB, 검증 기준은 IoT 이미지와 같다. 샘플 배열 반환. 같은 정규화 이미지를 다시 올리면 기존 샘플을 반환하고 새 작업을 만들지 않는다 |
| POST `/api/training/imports/video` | multipart `file`(MP4·MOV·M4V·WebM·MKV·AVI, 최대 500 MiB, 파일 머리글 검사), 선택 `title`, `intervalSeconds`(0.5~60, 기본 2), `maxFrames`(1~200, 기본 60). 202와 가져오기 작업 반환 |
| POST `/api/training/imports/youtube` | JSON `{url, title?, intervalSeconds, maxFrames}`. `youtube.com/watch?v=`, `youtu.be/`, `youtube.com/shorts/`만 허용하고 `https://www.youtube.com/watch?v=<11자 ID>`로 정규화해 저장한다. 202 |
| GET `/api/training/imports` | 최근 20개 `{id,kind,source,title,intervalSeconds,maxFrames,status,frames,error,createdAt,finishedAt}`. `leaseToken`은 항상 null |

- 샘플 응답에 `source`(`IOT`·`UPLOAD`·`VIDEO_FILE`·`YOUTUBE`)와 `importId`를 추가했다. 수동 입력 샘플의 `deviceId`·`cameraId`·`capturedAt`은 null이다.
- 가져오기 상태: `QUEUED` → `RUNNING` → `COMPLETED` 또는 `FAILED`. 실패 전까지 저장한 프레임은 유지한다. 처리 후 원본 영상은 삭제한다.
- 오류 코드: `INVALID_FILE_COUNT`, `INVALID_IMAGE`, `INVALID_IMAGE_SIZE`, `UNSUPPORTED_VIDEO`, `INVALID_VIDEO_SIZE`, `INVALID_YOUTUBE_URL`, `INVALID_IMPORT_OPTIONS`(400), `FILE_TOO_LARGE`(413).
- 워커 내부 경로(Bearer·leaseToken): `/internal/ai/imports/claim`, `/{id}/heartbeat`, GET `/{id}/video`, multipart `/{id}/frames`(`leaseToken`, `positionSeconds`, `file`), `/{id}/complete`, `/{id}/fail`. 같은 작업의 동일 프레임은 한 번만 저장하고 `maxFrames` 초과는 409 `FRAME_LIMIT`다.

## 내부 연결

외부 프록시에 `/internal/**`를 공개하지 않는다. 워커는 loopback 8201에서 전용 Bearer로 `/internal/ai/jobs/claim`, `/jobs/{id}/heartbeat`, `/jobs/{id}/complete`, `/jobs/{id}/fail`을 호출한다. 뒤의 세 경로도 `/internal/ai` 접두사를 사용하며 작업별 leaseToken이 필요하다. 이미지 조회는 `/internal/ai/samples/{id}/image`다.

MediaMTX는 POST `/internal/media/auth` 콜백으로 장치 publish 또는 관리자 read 자격 증명을 검증한다. 이 경로는 워커 Bearer 경로와 분리한다.

## YOLO·Gemma 데이터셋·학습·평가 (2026-09-27)

아래 관리자 쓰기는 세션과 CSRF가 필요하다. 오류는 기존 JSON `error` 형식을 사용한다.

| API | 입력·결과 |
|---|---|
| GET /api/training/runs | 최근 40개 실행. id, name, state, summary(JSON 문자열), automation(JSON 문자열 또는 null), createdAt, updatedAt. 이미지 원본·전체 분석 스냅샷은 목록에 포함하지 않는다. |
| POST /api/training/runs | `{name, sampleIds, confidence, auto?}`. 이름 최대 100자, 중복 없는 LABELED 이미지 1~40장, confidence 0.5~0.95. PREPARING 상태로 202 반환. `auto: {epochs: 1~100, deviceId?}`를 주면 아래 자동 진행을 적용한다. |
| GET /api/training/runs/{id} | 실행 요약 조회 |
| POST /api/training/runs/{id}/train | `{epochs: 1~100}`. DATASET_READY에서만 TRAINING으로 전이. |
| POST /api/training/runs/{id}/evaluate | TRAINED에서만 EVALUATING으로 전이. 기준·후보 모델을 같은 test에서 평가. |
| POST /api/training/runs/{id}/release | EVALUATED이고 평가를 통과한 파일만 SHA-256 확인 후 후보 등록. |
| GET /api/training/models | 등록된 후보 최대 100개. id, sha256, sizeBytes, createdAt, name(기본 모델 라벨·파이프라인 또는 실행 이름), source(BASE·MANUAL·AUTO), candidateMap·baselineMap(평가 test mAP50–95, BASE는 평가 기준으로 쓰인 최근 값). |
| GET /api/training/deployments | 최근 배포 요청 40개 |
| POST /api/training/deployments/{id}/cancel | 적용 전 배포 요청 취소(CANCELED) |
| POST /api/training/models/base | 설정한 현장 기본 모델(`BASE_MODEL_PATH`)을 BASE 후보로 등록. 해시가 장치 보고값·평가 기준 모델과 같아야 하며(아니면 409 `BASE_HASH_UNVERIFIED`), 같은 해시면 기존 후보 반환 |
| GET /api/training/device-policies | 장치별 `{policy, latestDeployment, drift}`. policy는 deviceId, name, modelName, activeSha256, online, mode, pinnedModelId, pinnedSha256, note, updatedBy, updatedAt |
| POST /api/training/device-policies/{deviceId} | `{mode: AUTO\|PINNED, modelId?, note?}`. 결과 `{deviceId, mode, pinnedModelId, deployment: REQUESTED\|IN_PROGRESS\|ALREADY_ACTIVE\|NONE, deploymentId}` |
| GET /api/training/device-policies/{deviceId}/history | 정책 변경 이력 최근 20개 |
| POST /api/training/deployments | `{modelId, deviceId}`. 등록된 장치별 활성 요청 하나만 허용. 202 반환. |

학습 작업 상태는 PREPARING → DATASET_READY → TRAINING → TRAINED → EVALUATING → EVALUATED다. 워커 실패나 임대 만료 시 FAILED로 끝나며 새 실행으로 다시 구성한다. 다른 작업이 활성 상태면 409 TRAINING_BUSY, 단계 불일치는 409 RUN_STATE_CONFLICT다. 데이터셋 생성은 실패한 이미지에 허위 박스나 빈 배경 라벨을 만들지 않고 제외한다.

### 자동 진행 (2026-09-27 저녁)

`auto`가 있는 실행은 PREPARE 완료 시 TRAIN(`auto.epochs`), TRAIN 완료 시 EVALUATE를 같은 트랜잭션에서 등록한다. EVALUATE 완료 후 별도 트랜잭션으로 후보 등록을 시도하고, `deviceId`가 있으면 해당 장치에 배포를 요청한다. 결과는 summary의 `automation`에 기록한다.

| automation 값 | 의미 |
|---|---|
| `{release: "REGISTERED", deployment: "REQUESTED", deploymentId}` | 후보 등록과 PENDING 배포 요청 완료 |
| `{release: "REGISTERED", deployment: "SKIPPED_NO_DEVICE"}` | 장치 미지정으로 후보 등록까지만 진행 |
| `{release: "STOPPED", reason}` | 등록 거부. 평가 미달은 `EVALUATION_REJECTED` |
| `{release: "REGISTERED", deployment: "STOPPED", reason}` | 등록 후 배포 요청 거부. 예: `DEPLOYMENT_ACTIVE`, `DEVICE_NOT_REGISTERED` |

어느 단계든 실패하면 기존과 같이 FAILED로 멈춘다. 평가 기준(기준 mAP50–95 이상)은 자동 진행에서도 완화하지 않는다. 배포 요청은 IoT가 명시적 CLI로 가져가야 적용되며 중앙이 장치 모델을 직접 교체하지 않는다.

주기 트리거는 `AUTO_PIPELINE_ENABLED=true`일 때만 동작한다. 60초마다 활성 학습 작업이 없고, 이전 실행 스냅샷에 포함된 적 없고 Gemma 태그가 하나 이상인 LABELED 이미지가 `AUTO_PIPELINE_MIN_SAMPLES`(기본 10) 이상이면 최근 최대 40장으로 자동 실행을 만든다. `AUTO_PIPELINE_EPOCHS`(기본 1), `AUTO_PIPELINE_CONFIDENCE`(기본 0.6), `AUTO_PIPELINE_DEVICE_ID`(기본 없음)를 사용한다. 실패한 실행의 이미지도 재사용하지 않아 같은 입력으로 반복 실패하지 않는다.

워커는 기존 `/internal/ai/**` Bearer 인증을 사용한다. `/internal/ai/training/tasks/claim`은 작업 또는 204를 반환하며 `/tasks/{id}/heartbeat`, `/complete`, `/fail`에는 `leaseToken`이 필요하다. complete는 JSON 문자열 `summary`, fail은 최대 500자의 `error`를 받는다. 임대는 120초, 갱신은 20초다. 전체 스냅샷은 내부 `/internal/ai/training/runs/{id}`에서만 제공한다.

## IoT 모델 배포 계약 (2026-09-27)

장치 Bearer 인증을 사용한다. 다른 장치 요청은 404로 차단한다. 장치가 요청하기 전에는 모델을 전송하지 않는다. 배포 알림은 heartbeat 응답으로 전달한다(중앙이 IoT로 접속하지 않음).

- heartbeat 응답의 `deployment`: 이 장치의 미완료 배포(PENDING~APPLYING)가 있으면 아래 desired와 같은 객체, 없거나 취소·종료되면 null. IoT는 별도 CLI 실행 없이 이 값을 보고 배포를 시작·재개한다. 같은 id가 계속 오면 진행 중인 작업을 이어가며 중복 실행하지 않는다.
- `GET /api/iot/deployments/desired`: 미할당이면 204. 할당 시 `{id, modelId, modelName, deviceId, status, sha256, sizeBytes, format:"pt", classes:["person","safety_vest","traffic_cone","hard_hat"], downloadPath}`. `modelName`(2026-09-28 추가)은 모델 현황판과 같은 표시 이름(기본 모델 라벨 → 파이프라인 이름 → 학습 실행 이름, 최대 200자)이며 이름이 없으면 modelId와 같다. IoT는 적용 완료 후 이 값을 heartbeat `modelName`으로 보고한다. 이 필드를 모르는 IoT는 무시해도 된다.
- `GET /api/iot/deployments/{id}/model`: 지정 장치의 미완료 배포 파일. 최대 512 MiB, octet-stream, Content-Length, no-store. 서버에서도 파일 해시를 다시 검사한다.
- `POST /api/iot/deployments/{id}/status`: `{status, activeSha256, message}`. message 최대 500자. 활성 해시는 null 또는 소문자 64자리 SHA-256. 성공 `{accepted:true}`.

정상 전이는 PENDING → DOWNLOADING → VALIDATING → APPLYING → APPLIED다. 비완료 단계에서 FAILED, APPLYING에서 ROLLED_BACK을 허용한다. APPLIED의 activeSha256은 목표 모델과 일치해야 한다. 동일 상태·동일 활성 해시의 재전송은 성공한다. 다른 순서·내용은 409로 차단한다. 완료된 배포는 desired에서 빠지고 파일 재다운로드도 차단된다. 관리자는 적용 전 단계(PENDING·DOWNLOADING·VALIDATING)의 배포를 `POST /api/training/deployments/{id}/cancel`(세션+CSRF)로 CANCELED 처리할 수 있다. 이미 CANCELED면 그대로 성공, APPLYING 이후·종료 상태는 409 `DEPLOYMENT_NOT_CANCELABLE`, 없는 id는 404다. 취소 후 IoT의 desired·파일·상태 보고는 종료 배포와 같이 빠지거나 409로 막힌다(V19). 현장 적용은 IoT의 후보 검증·원자적 전환·복구 코드와 별도 검증해야 한다.

## 학습 파이프라인 (2026-09-27 밤)

관리자 세션+CSRF. 마법사로 만든 파이프라인은 수집 → 자동 라벨링 → 데이터셋 구성까지 5초 주기로 진행하고, 학습 실행(training_runs)을 만든 뒤에는 기존 자동 진행(구성 → 학습 → 평가 → 통과 시 후보 등록)을 따른다. 배포는 목록에서 관리자가 요청한다(기존 `POST /api/training/deployments`).

| API | 동작 |
|---|---|
| GET `/api/training/pipelines` | 최근 30개 `{pipeline, run, release, deployments}` |
| POST `/api/training/pipelines` | `{name, sourceKind: IOT\|UPLOAD\|EXISTING\|ROBOFLOW, source, external:[{id}], options:{baseModel, epochs(1~100), confidence(0.5~0.95)}}` 202. IOT는 `deviceIds`·`targetCount`(1~200)·`includeExisting`, UPLOAD는 업로드 API가 돌려준 `sampleIds`·`importIds`, ROBOFLOW는 `external` 1개 이상(수집·라벨링 없이 데이터셋 단계로 진행, V14) |
| POST `/api/training/pipelines/{id}/cancel` | 수집·라벨링·데이터셋 대기 단계에서만 취소. 그 외 409 `PIPELINE_ALREADY_STARTED` |
| DELETE `/api/training/pipelines/{id}` | 끝난(취소·실패·평가 미달) 파이프라인과 학습 실행·작업 기록·실행 폴더 삭제, 204. 진행 중 409 `PIPELINE_ACTIVE`, 등록 모델 있음 409 `MODEL_REGISTERED`, 없음 404 `PIPELINE_NOT_FOUND`. 수집·업로드 이미지는 남기며, 지운 실행에 쓰였던 이미지는 다시 미사용으로 집계된다 |
| POST `/api/training/runs/{id}/stop` | 진행 중 실행 중단. 작업을 FAILED로 바꾸면 워커 임대 갱신이 실패해 학습 자식 프로세스를 종료한다. summary에 `stopped:true`. 진행 중이 아니면 409 `RUN_NOT_ACTIVE` |
| GET `/api/training/runs/{id}/metrics` | YOLO `results.csv`의 `{columns, rows}`(에포크별 손실·mAP·정밀도·재현율·학습률). 없으면 빈 배열 |
| GET `/api/training/base-models` | `[{key,label,description,kind,size,map,createdAt}]`. key는 `default`(현장 기본 모델), `yolo26n`·`yolo26m`(사전학습), `release:<id>`(등록 후보). kind `SITE\|PRETRAINED\|RELEASE`, size는 규모·파일 크기, map은 등록 후보의 기준 test mAP50–95(그 외 null) |
| GET·POST `/api/training/external-datasets` | Roboflow Universe `{url}`만 허용(universe.roboflow.com/워크스페이스/프로젝트[/dataset/버전]). 같은 버전 재요청은 기존 항목 반환. 기준 데이터셋에 이미 포함된 construction-site-safety v27은 409 `ALREADY_IN_BASELINE`이며 목록에서도 숨긴다 |

- 파이프라인 단계: `COLLECTING` → `LABELING` → `DATASET` → `RUN`, 실패 `FAILED`, 취소 `CANCELLED`. EXISTING은 이전 실행에 쓰지 않은 태그 있는 LABELED 이미지(최대 200장)를 쓴다.
- 학습 작업은 전역 1개만 실행하므로 다른 학습 중에는 데이터셋 단계에서 기다린다.
- 외부 데이터셋은 워커(`ai-worker/external.py`)가 `scripts/yolo_datasets.py`로 `.artifacts/ai-assets/datasets/roboflow`에 받고 같은 버전은 재사용한다. 키는 중앙 `.env`의 `ROBOFLOW_API_KEY`다. 클래스는 이름으로 자동 매핑한다(부정 클래스 NO-* 제외, person·worker → person, vest → safety_vest, cone → traffic_cone, hat·hardhat·helmet → hard_hat). 선택한 데이터셋은 원본 train·val을 train·val에, 원본 test는 참고 평가용 `ext_test`에 넣는다. 배포 판정 test는 기준 자료만 쓴다.
- 외부 데이터 상한: train은 기준 train 수와 3,000장 중 작은 값, val은 기준 val 수, ext_test는 200장. (데이터셋 id, split, 파일명) 해시 순서로 뽑는다.
- 누적 이어학습(2026-09-28): 베이스가 `release:<id>`이고 그 실행의 학습 스냅샷이 남아 있으면, 기준 자료가 아닌 이미지(수집 선별·외부)를 train·val·site_test·ext_test 같은 split으로 이어받는다. 부모도 그 부모를 이어받았으므로 자료가 누적된다. 기준 test는 이어받지 않고 매번 기준 자료에서 넣는다. 이번 실행에 같은 파일명이 이미 있으면 건너뛰고, 이어받은 수는 외부 상한에 포함하지 않는다. 스냅샷이 없는 BASE 후보·`default`·사전학습 베이스는 이어받지 않는다. 준비 summary의 `inherited`(split별 수)·`inheritedFrom`(부모 실행 id)에 기록한다.
- 부분 라벨 보정: 원본에 없는 클래스는 현장 기본 4클래스 모델 박스(신뢰도 0.6 이상)로 채우고, 원본이 라벨한 클래스의 예측은 버린다. 안전모는 원본이 얼굴까지 머리 전체를 감싸는 경우가 있어(hard-hat-workers 기준 박스 높이 약 1.8배), 원본 박스 안에 중심이 있는 기존 모델 안전모 박스로 좌표를 바꾸고 대응 박스가 없으면 이미지를 제외한다. 없는 클래스에 0.25~0.6 박스가 있으면 이미지 전체를 제외한다. 실행 summary의 `externalExcluded`가 제외 수다. 모델이 아예 놓친 객체는 배경으로 남으며 사람 검토는 없다.
- 평가 결과의 `external`은 ext_test 기준 점수·클래스별 mAP50–95다. 채운 클래스 박스가 기존 모델에서 왔으므로 참고용이며 배포 판정에 쓰지 않는다.
- 평가 비교 대상: 베이스가 4클래스 모델이면 그 모델, 사전학습(COCO)이면 현장 기본 모델. 기준 mAP50–95 이상 조건은 그대로다.
- 학습 기본값: 마법사 epoch 100, imgsz 640, batch 8(고정), AdamW lr0 0.0001.
- 수집 이미지 자동 분할: 자동 선별을 통과한 수집 이미지를 묶음(같은 영상 가져오기, 같은 장치·카메라의 10분 구간, 직접 올린 이미지는 한 장씩) 단위로 train/val/test 70/15/15 나눈다. 묶음이 5개 미만이면 모두 train. 현장 test는 `images/site_test`에 따로 두며 배포 판정용 기준 test 82장과 섞지 않는다.
- 현장 test가 10장 이상이면 평가 결과에 `evaluation.site`(기준·후보 mAP, `deltaMap50_95`)를 참고로 기록한다. 라벨이 현장 기본 모델의 박스 초안에서 오므로 기본 모델에 유리하게 나올 수 있고, 배포 판정에는 쓰지 않는다.
- 이름: 파이프라인 이름은 입력받지 않는다(V15). 학습 실행이 평가 완료·실패했거나 학습 전에 실패·취소되면 워커가 Gemma(`gemma4:12b`)로 40자 이내 이름을 추천해 파이프라인과 학습 실행에 함께 적용한다. 실패하면 규칙 이름(예: `Roboflow-Y26n-3ep`), 중복이면 ` #2`를 붙인다. 내부 `/internal/ai/pipelines/naming/claim`, `/internal/ai/pipelines/{id}/name`.

## 전역 자동 파이프라인·빈 이미지 삭제 (2026-09-27 밤, V17)

- 라벨링 완료 결과에 4개 클래스 태그가 하나도 없으면 샘플·라벨링 작업·검토 이력·이미지 파일을 삭제하고 `discarded_samples`에 출처·장치·사유(`NO_OBJECTS`)만 남긴다. 모든 출처에 적용한다. 파일은 커밋 뒤 삭제하며 중간에 멈추면 기동 시 `pending_media_deletions`로 정리한다.
- 자동 파이프라인(설정 1행): 켜짐·실행 시각(KST, 기본 02:00)·최소 장수(기본 50)·epoch(기본 100)·베이스 정책(`LATEST` 최근 등록 후보 이어학습, 없으면 기본 모델 / `DEFAULT`). 매분 확인하며 하루 한 번, 여러 IoT가 보낸 미사용 태그 이미지(source=IOT, 최대 500장)가 최소 장수 이상이고 다른 학습·파이프라인이 없을 때 `sourceKind=AUTO` 파이프라인을 만든다. 부족하면 그날 결과만 기록한다. 기존 `AUTO_PIPELINE_*` 환경변수 트리거는 제거했다.
- GET `/api/training/auto-pipeline`: `{settings, pending, labelingQueued, discardedToday, devices, onlineDevices, nextRunAt, latestPipelineId}`. PUT(세션+CSRF): `{enabled, runAt:"HH:MM", minSamples(1~1000), epochs(1~100), basePolicy}`. POST `/api/training/auto-pipeline/run`: 시각과 무관하게 새 이미지 1장 이상이면 202, 없으면 409 `NO_NEW_IMAGES`, 학습 중이면 409 `TRAINING_BUSY`.
- 새로운 파이프라인 마법사에서는 IoT 수집 선택지를 뺐다(API의 IOT 방식은 호환용으로 유지). IoT는 사람 감지 시에만 전송하도록 iot-server 프로젝트에 인계했다.

## 장치 감시 제어 (2026-09-27 밤, V18)

- POST `/api/devices/{deviceId}/monitoring` (관리자 세션+CSRF): `{"action":"PAUSE"|"RESUME"}` → 갱신된 장치. 없는 장치 404 `DEVICE_NOT_FOUND`. 요청할 때마다 새 commandId를 만든다.
- GET `/api/devices` 항목에 `monitoringAction`, `monitoringRequestedAt`을 추가했다. 화면은 실제 상태를 `runtimeStatus`로 보여 주고, 요청이 2분 안에 반영되지 않으면 "IoT가 요청을 아직 반영하지 않음"으로 표시한다.
- 중앙은 IoT에 직접 접속하지 않는다. IoT 웹 화면(8100)에서 직접 바꾼 상태는 오래된 중앙 명령이 다시 덮어쓰지 않는다(commandId 비교).

## 장치별 배포 정책 (2026-09-28, V20)

관리자 세션+CSRF. 장치마다 정책 하나를 둔다. 정책 행이 없으면 **PINNED(현재 모델 유지)**가 기본이다.

| 정책 | 관리자 배포 요청(`POST /api/training/deployments`) | 자동 파이프라인 배포 |
|---|---|---|
| PINNED, 고정 모델 없음 | 409 `DEVICE_PINNED` | 차단, automation `{deployment:"STOPPED", reason:"DEVICE_PINNED"}` |
| PINNED, 고정 모델 X | X 재배포만 허용, 다른 모델 409 `DEVICE_PINNED` | 차단 |
| AUTO | 허용 | 허용 |

- 정책 변경(`POST /api/training/device-policies/{deviceId}`)은 장치 행을 잠근 트랜잭션에서 처리한다.
  - PINNED로 바꾸면 고정 모델이 아닌 적용 전(PENDING·DOWNLOADING·VALIDATING) 배포를 CANCELED로 바꾼다. APPLYING이면 409 `DEPLOYMENT_APPLYING`이며 정책도 바뀌지 않는다.
  - 고정 모델이 있으면: 같은 모델 배포가 진행 중이면 `IN_PROGRESS`, 장치 보고 활성 해시와 같으면 `ALREADY_ACTIVE`, 아니면 배포를 만들고 `REQUESTED`.
  - AUTO는 고정 모델을 비우고 진행 중 배포를 그대로 둔다.
  - 모든 변경은 `device_model_policy_history`에 남는다. 없는 장치 404, 없는 모델 409 `MODEL_NOT_FOUND`.
- `drift`: 고정 모델이 있고 장치가 보고한 activeSha256이 그 해시와 다르면 true. 자동으로 되돌리지 않으며 관리자가 고정 모델 재배포를 요청한다.

## 원본 녹화·구간 재생 (2026-09-30, V23)

영상 분석·검색 P1-a. 녹화 1건 = 장치·카메라·촬영 시작/종료 시각이 있는 MP4 파일 하나다. 재생 위치는 `요청 시각 - startedAt`(초)이며 범위는 `[startedAt, endedAt)`이다. 현재 등록 경로는 테스트 영상용 워커 API뿐이고 MediaMTX 녹화 연결(P1-b)은 미구현이다.

| API | 인증 | 동작 |
|---|---|---|
| POST `/internal/ai/recordings` | 워커 Bearer(loopback 8201) | multipart `file`(MP4 `ftyp` 머리글, 최대 500 MiB), `deviceId`, `cameraId`, `startedAt`(ISO 8601), `durationSeconds`(0 초과 86400 이하). 녹화 반환. 같은 장치·카메라·시작 시각에 같은 파일·길이를 다시 올리면 기존 녹화, 다르면 409 `RECORDING_CONFLICT` |
| GET `/api/recordings?deviceId=&cameraId=` | 관리자 세션 | 최근 시작 순 최대 100건 |
| GET `/api/recordings/{id}` | 관리자 세션 | 녹화 한 건 |
| GET `/api/recordings/locate?deviceId=&cameraId=&at=` | 관리자 세션 | `{recording, offsetSeconds, playbackPath}`. 해당 시각을 포함한 녹화가 없으면 404 `NO_RECORDING`. 겹치면 가장 늦게 시작한 녹화 |
| GET `/api/recordings/{id}/video` | 관리자 세션 | `video/mp4`, Range 요청 시 206, `Cache-Control: no-store, private` |

- 녹화 필드: `{id, deviceId, cameraId, startedAt, endedAt, durationSeconds, source:"FILE", sha256, sizeBytes, createdAt}`. 시각은 마이크로초 단위로 저장한다.
- 오류 코드: `INVALID_CAMERA`, `INVALID_DURATION`, `INVALID_VIDEO_SIZE`, `UNSUPPORTED_VIDEO`(400), `DEVICE_NOT_FOUND`, `RECORDING_NOT_FOUND`, `RECORDING_FILE_MISSING`, `NO_RECORDING`(404).
- 파일은 `MEDIA_ROOT/recordings/{id}.mp4`에 둔다. 장치를 삭제하면 녹화 행도 삭제되지만 파일은 남으므로 정리 스크립트가 함께 지운다.
- 등록 CLI: `.venv/bin/python scripts/register-recording.py <mp4> --device <id> --camera <id> [--started-at <UTC ISO>]`. 길이는 OpenCV 프레임 수/fps로 계산한다.

## 탐지 배치·사람-보호구 연결 (2026-09-30, V24)

영상 분석·검색 P2. IoT가 한 카메라의 최대 60초 구간 YOLO·추적 결과를 배치로 보낸다. 현재 송신자는 테스트 영상용 모의 IoT(`scripts/simulate-video-iot.py`)뿐이며 실제 IoT 구현은 없다(iot-server 인계 전).

| API | 인증 | 동작 |
|---|---|---|
| POST `/api/iot/detections` | 장치 Bearer | 배치 저장·연결 요약. `{accepted:true, duplicate, batchId}` |
| GET `/api/detections/tracks?deviceId=&cameraId=&from=&to=` | 관리자 세션 | `[from,to)`와 겹치는 추적 요약(최대 1,000건, 기간 1일 이하). 첫 관찰 시각·배치·추적 ID 순 |

요청 본문:

```json
{"batchId":"uuid","deviceId":"...","cameraId":"...","sessionId":"uuid",
 "startedAt":"UTC ISO","endedAt":"UTC ISO","model":"ppe4-...","tracker":"bytetrack",
 "cuts":["UTC ISO"],
 "frames":[{"at":"UTC ISO","objects":[{"cls":"person","score":0.93,"box":[x1,y1,x2,y2],"trackId":"s3-t1"}]}]}
```

- 구간은 `[startedAt,endedAt)`, 60초 이하. 프레임 최대 600개·시각 오름차순·구간 안, 프레임당 객체 최대 100개. 박스는 0~1 정규화이며 x1<x2, y1<y2. `cls`는 소문자·밑줄(연결 규칙은 `person`·`hard_hat`·`safety_vest`만 사용).
- `trackId`는 `person`에만 쓸 수 있고 한 프레임에서 중복될 수 없다. 없으면 추적되지 않은 사람으로 센다. 같은 `sessionId` 안에서 추적 ID가 이어지며 배치 경계를 넘는 추적은 배치마다 요약 한 행씩 생긴다.
- `cuts`는 장면 전환 시각이다. 한 추적 ID가 컷 앞뒤에 모두 나오면 거부한다(IoT는 컷에서 추적기를 초기화하고 새 ID를 쓴다).
- 인증 장치와 `deviceId`가 다르면 403 `DEVICE_MISMATCH`, 등록 전 장치는 409 `DEVICE_NOT_REGISTERED`. 오류 코드(400): `INVALID_WINDOW`, `FUTURE_BATCH`, `FRAME_OUTSIDE_WINDOW`, `FRAMES_NOT_SORTED`, `INVALID_BOX`, `INVALID_TRACK`, `CUT_OUTSIDE_WINDOW`, `TRACK_SPANS_CUT`, 형식 오류 `INVALID_REQUEST`.
- 재전송: 같은 `batchId`·같은 내용은 추가 저장 없이 `duplicate:true`, 내용이 다르거나 다른 장치면 409 `BATCH_ID_CONFLICT`.
- 원본 프레임·컷은 `detection_batches.payload`에 보존한다. 규칙을 바꾸면 다시 계산할 수 있다.

연결 규칙 `ppe-assoc-v1`(중앙 `DetectionService`):

| 항목 | 규칙 |
|---|---|
| 안전모 후보 | 박스 중심이 사람 박스 가로 범위 안, 세로 `y1-0.10h ~ y1+0.35h`(머리 영역), 안전모 높이 0.5h 이하 |
| 조끼 후보 | 박스 중심이 사람 박스 가로 범위 안, 세로 `y1+0.15h ~ y1+0.85h` |
| 배정 | 보호구 박스 하나는 머리(`y1+0.12h`) 또는 몸통(`y1+0.45h`) 기준점에 가장 가까운 사람 한 명에게만 |
| 상태 | 추적의 관찰 프레임 중 2프레임 이상·20% 이상 연결 → `present`, 그 밖은 `unknown`. **YOLO 근거로 `absent`를 만들지 않는다** |
| 미연결 | 머리 영역 밖 안전모(손에 든 경우 등)·몸통 밖 조끼는 배치의 `unassigned_hard_hats`·`unassigned_vests`로만 센다 |

추적 요약 필드: `{batchId, sessionId, trackId, firstSeen, lastSeen, frames, maxScore, helmetFrames, vestFrames, helmet, vest, ruleVersion}`. `absent` 판정은 P3 Gemma 구간 분석이 시각 근거로만 만든다.

## 구간 분석·조건 검색·질문 대화 (2026-09-30, V25)

영상 분석·검색 얇은 한 바퀴(P3·P4·P5 최소). Gemma는 ① 10초 구간의 사람별 안전모·조끼 판정과 요약, ② 자연어 질문 → 검색 조건만 만든다. 검색은 중앙 PostgreSQL이 한다. 워커는 `ai-worker/observer.py`(dev.py가 `observer`로 실행)이며 기존 `worker.py`와 분리되어 학습 작업에 막히지 않는다.

| API | 인증 | 동작 |
|---|---|---|
| POST `/internal/ai/observations/claim` | 워커 Bearer | 구간 작업 임대(120초). 없으면 204. `{batchId, leaseToken, deviceId, cameraId, startedAt, endedAt, recordingId, recordingPath, offsetSeconds, durationSeconds, detections:{frames,cuts}, tracks:[{trackId,frames,helmet,vest}]}` |
| POST `/internal/ai/observations/{batchId}/heartbeat`·`/complete`·`/fail` | 워커 Bearer | 임대 연장·결과 저장·실패. 임대가 없거나 다르면 409 `LEASE_LOST` |
| POST `/internal/ai/search/questions/claim` | 워커 Bearer | 질문 임대(90초). `{id, leaseToken, message, now, history:[{message,filter}], cameras:[{deviceId,cameraId,firstAt,lastAt,segments}]}` |
| POST `/internal/ai/search/questions/{id}/answer`·`/fail` | 워커 Bearer | `{leaseToken, filter, reply, elapsedMs}` 또는 `{leaseToken, error}` |
| GET `/api/observations/search?deviceId=&cameraId=&from=&to=&helmet=&vest=&text=` | 관리자 세션 | 분석 완료 구간 검색(최신순 최대 50개, 시작 시각 내림차순) |
| POST `/api/search/questions` | 관리자 세션+CSRF | `{conversationId?, message(≤500)}`. 대화가 없으면 새로 만든다. 같은 대화에 답을 기다리는 질문이 있으면 409 `QUESTION_PENDING`, 없는 대화 404 |
| GET `/api/search/questions/{id}` | 관리자 세션 | 질문 상태. COMPLETED면 `filter`·`reply`와 그 조건의 `results`(조회할 때마다 다시 검색) |
| GET `/api/search/conversations/{id}` | 관리자 세션 | 대화의 질문 목록(결과 제외) |

- 작업 생성: 탐지 배치를 저장할 때 같은 트랜잭션에서 구간 작업(`observations`, batchId 동일)을 만든다. 구간 시작 시각을 포함한 원본 녹화가 있어야 워커가 가져가며, 없으면 QUEUED로 기다린다.
- 구간 분석: 녹화에서 구간 안 균등 간격 4장을 읽고, 0.3초 이내 탐지 프레임의 추적 사람 박스와 ID 라벨을 그려 Gemma에 준다. 표본에 박스가 보인 추적만 사람 참조(JSON 스키마 enum)로 쓴다. 4장 모두 평균 밝기 12 미만이면 SKIPPED(`dark`).
- 완료 검증: 사람 참조는 그 배치의 추적 ID만, 중복 불가(400 `UNKNOWN_TRACK`), 상태는 present/absent/unknown(400 `INVALID_STATE`), 완료는 요약 필수. 다시 분석하면 사람 판정은 최신 한 벌만 남는다. 임대 만료는 3회까지 재시도 후 FAILED.
- 사람 판정 필드: `{trackId, helmet, vest, evidence, yoloHelmet, yoloVest}`. `yolo*`는 P2 연결 규칙 결과이며 검색 조건에는 Gemma 판정을 쓴다.
- 검색 조건 `filter`: `{deviceId, cameraId, from, to, helmet, vest, text}`. null은 조건 없음. from·to가 없으면 그쪽 제한이 없고 둘 다 없으면 전체 기간이다. 둘 다 있으면 to가 from보다 뒤여야 한다(400 `INVALID_RANGE`). helmet·vest는 같은 사람이 두 조건을 모두 만족해야 한다. `text`는 요약·근거의 부분 일치(LIKE 특수문자는 글자 그대로).
- 결과 구간: `{batchId, deviceId, cameraId, startedAt, endedAt, summary, persons, matched, recordingId, offsetSeconds, playbackPath}`. `matched`는 보호구 조건에 맞은 추적 ID, `offsetSeconds`는 녹화 안 재생 위치.
- 질문 해석: Gemma는 날짜(YYYY-MM-DD)·24시간 시각·"쯤" 여부·보호구·문구만 고르고, 한국 시간 기간 계산은 워커 코드가 한다("쯤"은 ±30분, 시각 하나는 1시간, 날짜만이면 하루). 사람·작업자·장면 같은 일반 단어는 문구 조건에서 뺀다. 질문과 앞 대화에 날짜·시간 표현이 없으면 기간 조건을 만들지 않는다(전체 기간). 이어지는 질문은 같은 대화의 최근 5개 조건을 함께 준다. 질문 임대가 만료되면 다시 시도하지 않고 FAILED로 알린다.

### 에이전트형 화면 보강 (2026-09-30, V26)

| API | 인증 | 동작 |
|---|---|---|
| GET `/api/observations/{batchId}/thumbnail` | 관리자 세션 | 구간 대표 이미지(JPEG, 추적 박스·ID 표시). 없으면 404 `THUMBNAIL_NOT_FOUND` |
| POST `/api/search/questions/{id}/cancel` | 관리자 세션+CSRF | 답을 기다리는 질문을 CANCELED로. 끝난 질문 409 `QUESTION_FINISHED`, 없는 질문 404 |

- 구간 완료 `complete`에 선택 필드 `thumbnail`(base64 JPEG, 300KiB 이하, JPEG 머리글 검사 → 400 `INVALID_THUMBNAIL`). `MEDIA_ROOT/observations/{batchId}.jpg`에 저장하고 결과 구간에 `thumbnailPath`를 준다. 장치를 삭제해도 파일은 남으므로 정리 스크립트가 함께 지운다.
- 질문 답 `answer`에 선택 필드 `trace`(16KiB 초과 시 `{truncated:true}`): `{model, promptVersion, context, message, output(Gemma 원본 JSON), range, promptTokens, outputTokens, elapsedMs}`. 질문 조회 응답의 `trace`로 그대로 돌려주며 화면 "중간 단계"에 표시한다. 시스템 프롬프트 전문은 넣지 않는다.
- 질문 해석 `question-v2` 보강: 시각은 `HH:MM[:SS]`, 같은 시작·끝이면 그 1분. 장치·카메라 조건은 질문(또는 같은 대화의 앞 질문) 문장에 그 이름이 있을 때만 쓴다. 이어지는 질문에 넘기는 이전 조건은 하루 전체면 날짜만 보여 준다.

### 장애 복구·응답 검증·지난 대화 (2026-10-02, 마이그레이션 없음)

| API | 인증 | 동작 |
|---|---|---|
| POST `/internal/ai/observations/{batchId}/release` | 워커 Bearer | `{leaseToken, error}`. 분석 모델(Ollama) 연결 실패처럼 작업 자체의 문제가 아니면 시도 횟수를 되돌리고(1 감소, 최소 0) QUEUED로. 임대가 없거나 다르면 409 `LEASE_LOST` |
| POST `/api/observations/retry-failed?deviceId=` | 관리자 세션+CSRF | FAILED 구간을 QUEUED·시도 0회로 되돌린다(장치 생략 시 전체). `{requeued}`. 잘못된 장치 ID 400 `INVALID_DEVICE`. 이전 사람 판정은 다음 완료 때 교체된다 |
| GET `/api/search/conversations` | 관리자 세션 | 본인 대화 최근 50개(질문이 있는 대화만, 마지막 질문 시각 내림차순). `[{id, title(첫 질문), questions, createdAt, updatedAt}]` |
| DELETE `/api/search/conversations/{id}` | 관리자 세션+CSRF | 본인 대화와 질문 삭제 204. 없거나 다른 관리자 대화 404. 답을 기다리는 질문이 있어도 삭제되며 워커의 늦은 답은 저장되지 않는다 |

- 구간 작업 임대 응답에 `attempt`(이번 시도 번호, 1부터)를 추가했다.
- 서버 주기 정리(10초, `app.observations.sweep-ms`): 만료된 구간 임대는 3회까지 QUEUED, 이후 FAILED. 만료된 질문 임대는 FAILED. 워커가 60초 안에 가져가지 않은 QUEUED 질문은 FAILED("영상 분석 워커가 응답하지 않습니다"). 이전에는 워커가 다음 작업을 가져갈 때만 정리해 워커가 멈추면 작업이 그대로 남았다.
- 워커(`observer.py`): Ollama `/api/version`이 응답하지 않으면 작업을 가져가지 않는다. 분석 도중 연결 거부·끊김은 `release`, HTTP 오류·응답 시간 초과는 일반 실패(시도 횟수 사용)로 둔다. 고장 난 모델이 시도 횟수 소모 없이 같은 작업을 끝없이 되돌리지 않게 하기 위해서다. 질문 도중 연결 실패는 "분석 모델이 응답하지 않습니다"로 FAILED.
- 구간 분석 `segment-v2`: 응답을 저장 전에 검사해 길이 초과(`done_reason=length`)·JSON 아님·필수 필드 누락·모르는 사람 참조·허용되지 않은 판정값이면 `모델 응답 형식 오류: …`로 실패시킨다. 재시도(`attempt`>1)는 온도 0.4·seed=attempt로 바꿔 같은 오답 반복을 피한다.
- 이름 가림: Gemma가 프레임의 글자를 `screenText`로 따로 옮기게 하고(저장·로그 안 함), 성씨로 시작하는 세 글자 낱말을 이름 후보로 삼아 요약·근거에서 `○○○`로 바꾼다. 화면 글자와 무관하게 직함 앞 이름(`김철수 씨`), `/` 나열(`권도현/이영섭`), 괄호 안 나열(`(이영섭, 박민수)`)도 가린다. 규칙 기반이라 성씨로 시작하지 않는 이름·외국 이름·두 글자 이름은 놓칠 수 있다.
