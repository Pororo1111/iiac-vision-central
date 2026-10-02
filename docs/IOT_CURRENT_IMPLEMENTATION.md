# IoT 서버 구현 점검 (읽기 전용)

## 2026-09-27 작업 중단 시점 요약

- 최신 할 일과 완료 조건은 [개발 현황](DEVELOPMENT_PLAN.md), 계약·연동 결과는 [연동 문서](IOT_CENTRAL_HANDOFF.md)를 따른다. 아래 `b745f47` 점검은 과거 기준선이다.
- 원격 `c137838`: 명시적 모델 배포 CLI·모델/메타데이터 검사·중앙 계약 검증 코드. Python 테스트 56개 통과 보고와 Git 커밋을 확인했다.
- 실제 중앙에 검증용 이벤트·이미지를 재전송해 중복 방지를 확인했다. 가짜 모델 파일의 다운로드·해시·FAILED 종료 계약도 통과했다. 검증 자료·임시 토큰은 정리했다.
- `worker/spool.py`는 미추적 미완성 초안이다. 로컬 API 중단 중 워커 결과 영구 보관은 실제 흐름에 연결·검증되지 않았다.
- 수집 화면 브라우저 검증은 남아 있다. 원격 Node 부재로 화면 스크립트 검사도 실행하지 못했다.
- 종료 전 조회는 `paused`, `enabled=false`이며 `/ready` 503으로 운영 smoke test가 미통과했다. 12:11 KST의 실행 중 기록과 시점이 다르다. 운영 상태는 재개 시 다시 확인하며 이번 작업에서 설정·서비스·활성 모델을 변경하지 않았다.

## 과거 점검 기록

> 현재 구조와 접속 주소는 [통합 아키텍처](ARCHITECTURE.md)를 따른다. 아래는 시점별 점검·개발 요청·검증 기록으로, 이전 SAM3·수동 프롬프트·Nginx 개발 경로 설명을 현재 기능으로 해석하지 않는다.

> 2026-09-26 업데이트: 아래 내용은 커밋 `b745f47`을 조사한 2026-09-25 기준선이다. 이후 원격 작업트리에 중앙 등록·heartbeat·이미지/이벤트 outbox 초안과 실시간 영상 송출을 구현했다. 현재 `iot-jetson-001`은 웹캠 감시 중이며, 오버레이 H.264를 중앙 MediaMTX에 발행하고 중앙 관리자 화면에서 640×480 WebRTC 재생까지 확인했다. 최신 실행 상태는 원격 README와 중앙 `docs/VERIFICATION.md`를 우선한다.

기준 커밋: b745f47. 근거: `~/iot-server/AGENTS.md`, `README.md`, `app/main.py`, `worker/main.py`.

## 수집 이미지
- **영구 저장 미구현.** `worker/main.py`의 `main()`은 `result.plot()`으로 검출 박스를 그린 프레임에 침입 영역을 덧그린 뒤 `cv2.imencode('.jpg', annotated)`로 JPEG를 만들고 `jpeg_base64`로 로컬 `POST /internal/report`에 보낸다(약 289~321행). 원본 카메라 프레임은 별도로 저장하거나 전송하지 않는다. 기존 `assets/test-video.mp4`는 입력 영상이지 개별 수집 이미지가 아니다.
- `app/main.py`의 `ingest()`는 JPEG를 디코딩하지만, `latest, snapshot, received = payload, jpeg, now`로 최신 한 장만 프로세스 메모리에 둔다. `payload`는 `jpeg_base64`를 제외한다(약 245~275행). `preview()`의 `GET /api/snapshot`은 그 메모리 JPEG만 반환한다(약 284행). 재시작 시 미리보기는 사라진다.
- 이미지 수집 목록/ID별 원본·오버레이 조회/삭제 API, 이미지 파일 테이블·용량 제한·보관 기간·전송 현황 API는 없다. 따라서 중앙이 조회할 누적 이미지 수는 현재 0건이며, `/api/status`의 `frames_processed`는 프레임 추론 횟수이지 저장 이미지 건수가 아니다.

## 저장되는 메타데이터·이벤트
- `app/main.py`의 `lifespan()`이 만드는 SQLite 테이블은 `events(id, occurred_at, device_id, detections)`와 `settings(id, payload)`뿐이다(약 131~139행). 설정 payload에는 소스별 라바콘 영역이 포함된다. 이미지 BLOB/파일 경로는 없다.
- `ingest()`는 확정 영역 안에서 사람이 검출됐을 때 기본 30초 간격으로 침입 로그를 남기고 최근 1000건 초과 로그를 삭제한다(약 266~274행). `GET /api/events`는 이 로그의 payload를 반환하지만 이미지 바이트는 없다(약 278행). 이벤트 타입 `intrusion_started`/`intrusion_ended` 같은 상태 전이는 현재 없다. 로컬 로그 ID는 해당 프레임 `capture_id`이고 중앙 전송 이벤트 ID/카메라 ID/영역 ID 계약은 아직 없다.
- `README.md`의 '저장·인증' 및 'Jetson 배포' 설명도 이미지는 메모리만, SQLite에는 설정·영역·주기적 침입 이력만 저장한다고 명시한다.

## 중앙 통신·복구
- **중앙 등록, heartbeat, 침입 이벤트 전송, 이미지 전송, 영구 outbox 모두 미구현.** `app/main.py`의 HTTP 라우트는 로컬 상태·설정·영역·보고·이벤트·스냅샷용이다. `/api/iot/register`, `/api/iot/heartbeat`, `/api/iot/events`는 `AGENTS.md`의 목표 계약일 뿐 구현된 라우트가 아니다. `/api/status`는 `central_connected=False`를 반환한다(약 182행).
- `worker/main.py`의 `Connection.poll()`/`send()`(약 89~149행)는 로컬 API의 `/internal/config`, `/internal/zone`, `/internal/state`, `/internal/report`에만 요청한다. `self.report`는 최신 보고 하나를 메모리에 보관·교체한다. API 장애 중 오래된 프레임이나 침입 이벤트를 디스크에 쌓거나 재시작 후 재전송하지 않는다. `.local/worker-config.json`은 마지막 정상 감시 **설정 캐시**이며 이미지/이벤트 outbox가 아니다.

## 인증·관찰 및 현재 상태
- 내부 API는 `WORKER_TOKEN` Bearer와 장치 ID를 검증한다(`app/main.py`의 `authenticate()`, 약 116행). 브라우저 조회는 별도 로그인 없이 Tailscale 접근 정책에 의존하며 설정 변경은 커스텀 헤더와 교차 사이트 검사로 제한한다(`control_header()`, 약 123행). 인증값은 이 보고서에 포함하지 않았다.
- `GET /health`는 API 생존, `/ready`는 현재 설정의 최근 추론, `/api/status`는 워커/소스/모델/감지 상태, `/api/config`는 모드와 저장 영역을 보여준다. 현재 실행 중인 API·워커 사용자 서비스는 둘 다 active지만 감시 설정 `enabled=false`라 `/ready`는 503, 상태는 paused다. 현장 실물 라바콘 영역과 현재 수집 이미지가 있다고 가정하면 안 된다.
- Jetson은 현재 Tailscale 사설 IP의 HTTP 8100 포트로 직접 접속한다. Jetson HTTPS Serve는 설정되지 않았다(`AGENTS.md`, `README.md`).

중앙의 ‘IoT가 보낸 수집 이미지 현황 → 자유 프롬프트 Gemma 작업’ 흐름을 실제로 연결하려면 IoT 쪽에서 이미지 영구 저장, 원본/오버레이·시간·장치·카메라·영역 메타데이터, 조회·보관/삭제 정책, 중앙 업로드 및 장애 재전송 계약을 새로 구현해야 한다.
