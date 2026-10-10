# FIELD CONTROL — 중앙 서버

현장 IoT 장치 상태·침입 이벤트·수집 이미지와 실시간 웹캠을 관리하는 중앙 서버입니다.
로컬 개발에서는 Next.js가 API를 Spring Boot로 전달하며, 운영 구성은 Nginx를 사용합니다.

현재 구조의 기준 문서는 [통합 아키텍처](docs/ARCHITECTURE.md)입니다. 접속 주소·프로세스·데이터 흐름·인증·구현 범위를 함께 정리했습니다.

2026-09-27 작업 중단 시점의 완료 내용·남은 작업·완료 조건·재개 순서는 [개발 현황](docs/DEVELOPMENT_PLAN.md)을 참고하세요. 다음 구현은 IoT 워커 결과 영구 보관과 수집 화면 검증입니다.

## 구성

| 구성 | 버전 / 역할 |
|---|---|
| Java | Eclipse Temurin 21 LTS |
| Spring Boot | 4.0.8 / REST API |
| Spring Security | Spring Boot 관리 버전 / 세션·장치 인증 |
| MyBatis Starter | 4.0.1 / SQL·매핑 |
| PostgreSQL · pgvector | 17 · 0.8.7 / 영구 저장 · 오탐 판단 기억 유사도 검색 |
| Flyway | Spring Boot 관리 버전 / migration |
| Next.js · React | 16.3.5 · 19.3.0 / 관리자 화면 |
| Node.js | 22.22.1 / 프론트엔드 빌드·실행 |
| Nginx | 1.28.3 / 별도 운영 구성의 진입점 |

위 PostgreSQL·Node.js·Nginx 버전은 컨테이너 구성 기준이며, 로컬 Homebrew 설치 버전과 다를 수 있습니다.

`backend/`는 Java, `frontend/`는 Next.js, `nginx/`는 프록시 설정입니다.
`../iot-server`의 실제 추론 서비스와 `../sam3-yolo-pipeline`의 학습 프로젝트는 독립적입니다. 중앙 서버는 `../sam3-yolo-pipeline`을 참조하지 않습니다.

AI 워커·학습·영상 분석은 프로젝트 `.venv`(Python 3.12, `ai-worker/requirements.txt`)를 사용합니다. 기준 모델·사전학습 모델·기준 데이터셋·로보플로우 데이터셋은 Git 제외 `.artifacts/ai-assets/`에 두며 경로는 `scripts/ai_assets.py`에 모아 두었습니다.

자동 라벨링은 Gemma 설명·태그를 생성합니다. 후속 데이터셋 구성에서 기존 YOLO 박스와 함께 선별하고 학습·평가·후보 등록으로 연결합니다. SAM3는 사용하지 않습니다. 실행 방법은 [학습 가이드](docs/TRAINING_PIPELINE.md)를 따릅니다.

## 로컬 개발 실행 (Docker 사용 안 함)

```sh
brew install openjdk@21 postgresql@17 pgvector node@22 gradle
python3 scripts/init-env.py
python3 scripts/init-ai-env.py
PATH=/opt/homebrew/opt/node@22/bin:$PATH npm ci --prefix frontend
python3 scripts/install-mediamtx.py
python3 scripts/dev.py start
# 오탐 판단 기억에 Claude 판별 기본 사례(88건) 넣기(처음 한 번, 다시 실행해도 중복 없음)
.venv/bin/python scripts/seed-fp-memory.py
# 상태 / 종료
python3 scripts/dev.py status
python3 scripts/dev.py stop
```

접속은 `http://localhost:8200`, 수집 현황은 `/training-data`입니다. 기존 Tailscale HTTPS 9443 주소도 같은 로컬 서버로 연결됩니다. 관리자 계정은 `.env`를 사용하며 출력·커밋하지 않습니다. 프론트엔드는 Next.js 개발 서버, API는 Spring Boot, DB는 로컬 PostgreSQL 55432로 실행합니다. `.artifacts/local-dev/`에 개발 DB·이미지·로그를 보존합니다. Docker/Colima는 시작하지 않습니다.

실시간 영상은 Jetson이 Tailscale RTMP로 MediaMTX에 발행하고, 관리자 브라우저가 WHEP/WebRTC로 읽습니다. Spring Boot는 영상 바이트를 중계하지 않고 장치 publish 인증과 5분짜리 관리자 read 세션만 발급합니다. 녹화는 기본적으로 꺼져 있습니다.

| 주소 | 용도 |
|---|---|
| `https://<central-host>:9443` | 중앙 관리자 화면 |
| `https://<central-host>:9444` | 인증된 WHEP handshake |
| `http://<iot-ip>:8100` | IoT 현장 화면 |
| `<central-ip>:1935` | IoT 전용 RTMP publish (웹 접속 URL 아님) |
| `<central-ip>:8189` TCP/UDP | WebRTC ICE 미디어 |

`<central-host>`는 중앙 서버의 MagicDNS 이름, `<central-ip>`·`<iot-ip>`는 각 장비의 Tailscale IP입니다. 실제 값은 Git에 넣지 않고 `.env`에 둡니다. 비워 두면 로컬(127.0.0.1)에서만 접속합니다.

| `.env` 키 | 예시 | 사용처 |
|---|---|---|
| `PUBLIC_ORIGIN` | `https://<central-host>:9443` | Next.js dev 허용 출처, MediaMTX 허용 출처 |
| `MEDIA_PUBLIC_URL` | `https://<central-host>:9444` | 브라우저 WHEP 주소 |
| `MEDIA_HOST` | `<central-ip>` | RTMP·WebRTC ICE 바인딩 주소 |

`9443`, `9444`는 tailnet 전용 Tailscale Serve입니다. RTMP publish는 기존 장치 토큰으로 인증하고, 관리자 read 토큰은 카메라 보기 요청마다 새로 발급합니다. `/internal/media/auth`는 외부 Nginx 경로에서 공개하지 않습니다.

로컬 DB는 이전 Docker 볼륨과 분리되어 있습니다. 이전 검증 데이터는 Docker를 다시 기동할 때 migration으로 정리하며, 중지된 볼륨을 이미 삭제했다고 간주하지 않습니다. 상세 실행·동작은 [AI 파이프라인 문서](docs/AI_PIPELINE.md)를 참고하세요. 기존 아래 Docker 관련 명령은 운영 구성 참고용입니다.

## 인증과 초기 계정

관리자는 Spring Security의 서버 메모리 세션을 사용합니다. 쿠키는 HttpOnly·SameSite=Lax이며 운영 HTTPS에서는 Secure입니다. 서버 재시작 시 다시 로그인해야 합니다. CSRF 토큰은 `/api/auth/csrf`로 받아 로그인·로그아웃 요청 헤더에 넣습니다. JWT나 localStorage 토큰은 사용하지 않습니다.

IoT는 사전 발급된 장치별 Bearer 토큰으로 인증합니다. 토큰에 연결된 장치 ID만 등록·전송할 수 있습니다. DB에는 무작위 토큰의 SHA-256 해시만 저장하며 관리자 비밀번호는 BCrypt 해시로 저장합니다. 관리자 세션으로 IoT API를 호출하거나 IoT 토큰으로 관리자 API를 호출할 수 없습니다.

초기 실행에서 `.env`의 관리자와 IoT 한 장치를 생성합니다. 기존 레코드는 덮어쓰지 않습니다. 따라서 **기존 DB에서 `.env`의 비밀번호·토큰만 바꾸면 자격 증명이 변경되지 않습니다.** 계정 재설정·토큰 회전·추가 장치 관리 UI는 후속 범위입니다. DB 볼륨을 삭제하는 방식으로 운영 자격 증명을 바꾸지 마세요.

## 모의 IoT

실제 카메라 없이 서버 통신을 확인합니다. `.env`의 사전 발급 장치를 사용하며 표시 이름과 모델명을 시뮬레이터용으로 갱신합니다.

```sh
python3 scripts/simulate_iot.py --enqueue start
python3 scripts/simulate_iot.py --enqueue end
# 지속 heartbeat, 미전송 이벤트 재시도. Ctrl+C 종료.
python3 scripts/simulate_iot.py --watch
```

- heartbeat는 5초마다 전송하며 중앙은 마지막 수신 후 30초 초과 시 오프라인으로 표시합니다.
- 오프라인과 마지막 보고된 감지 상태는 구분합니다.
- 모의 이벤트는 `.artifacts/simulator.db`의 SQLite outbox에 먼저 기록합니다.
- 중앙 저장 확인 후 큐에서 삭제합니다. 실패·프로세스 재시작에도 UUID와 발생 시각을 유지합니다.
- 연결 실패는 최대 60초 지수 백오프, 인증·입력 오류는 큐를 보존하고 종료합니다.
- 모의 장치는 실제 침입 감지·경광등을 실행하지 않습니다.

아래 복구 검증은 Docker 운영 구성 참고용이며 현재 로컬 개발에서 실행하지 않습니다.

```sh
docker compose stop backend
# 연결 실패로 종료 코드 1을 반환하지만 이벤트는 outbox에 보존됩니다.
python3 scripts/simulate_iot.py --enqueue start
docker compose start backend
# backend가 healthy가 되면 재전송
docker compose ps
python3 scripts/simulate_iot.py
```

실제 원격 `iot-server`는 중앙 등록·heartbeat·카메라 상태와 실시간 영상까지 연결했습니다. 모의 장치는 카메라 없는 통신 회귀검증에 계속 사용합니다.

## API 계약

[docs/API.md](docs/API.md)에 필드·인증·응답·오류 코드를 정리했습니다.

## 검증

로컬 개발 서버와 PostgreSQL 55432를 대상으로 실행합니다. 별도 테스트 장치를 만들고 종료 시 해당 이벤트·이미지·장치를 삭제합니다. 작업 큐 테스트 동안 AI 워커는 중지해야 합니다.

```sh
CENTRAL_TEST_URL=http://localhost:8200 python3 tests/integration.py
CENTRAL_TEST_URL=http://localhost:8200 .venv/bin/python tests/labeling.py
```

검증 항목: 실제 PostgreSQL·Flyway·MyBatis, 로그인/로그아웃, CSRF, 세션 교체, 잘못된 자격 증명, 관리자/IoT 격리, 장치 ID 불일치, 입력 검증, 동시 중복 이벤트, 필터·페이지 처리.

`tests/recovery.py`는 기존 Docker 중단/복구 전용이며 현재 로컬 개발에서는 실행하지 않습니다.

브라우저에서는 로그인, 장치·이벤트 조회, 필터, 자동 갱신, 실시간 카메라, 로그아웃을 확인합니다. 경광등 검증은 이 서버의 범위에 포함하지 않습니다.

검증 결과는 [docs/VERIFICATION.md](docs/VERIFICATION.md)에 기록했습니다.

## Tailscale 사설망 접속

- 중앙 서버: MagicDNS 중앙 관제 `https://<central-host>:9443`
- IoT 서버: Tailscale IP 현장 화면 `http://<iot-ip>:8100`

중앙 웹 URL은 MagicDNS, IoT 웹 URL은 현재 Tailscale IP로 안내합니다. 같은 tailnet 연결·접근 정책 허용과 관리자 로그인이 필요합니다.

현재 연결은 Tailscale Serve HTTPS 9443 → 로컬 Next.js 8200 → `/api/**` Spring Boot 8201입니다. 영상 연결은 HTTPS 9444 → MediaMTX 8889입니다. TLS는 Tailscale Serve가 종료합니다.

- 기존 Serve 설정 확인: `tailscale serve status`.
- 재부팅 후에는 로컬 개발 프로세스를 `python3 scripts/dev.py start`로 실행합니다. Ollama와 모델도 별도 준비해야 합니다.
- `scripts/expose-tailscale.py`는 Docker Compose를 시작하는 이전 운영 구성용 스크립트이므로 현재 로컬 개발에서는 실행하지 않습니다.
- `dev.py`는 실행 환경에서 `COOKIE_SECURE=false`를 사용합니다. 이는 Tailscale HTTPS 접속에도 동일하며 운영의 Secure 쿠키 설정과 구분합니다.
- 기존 Gradio 443·IoT 8443 Serve 설정은 보존합니다. 현재 IoT 접속 안내는 위 IP 주소를 사용합니다. 전체 `serve reset`이나 Funnel 공개 설정을 사용하지 않습니다.
- HTTPS 검증 대상은 `CENTRAL_TEST_URL=https://<central-host>:9443`으로 지정합니다. Docker 전용 복구 테스트는 현재 로컬 개발에서 실행하지 않습니다.

## 운영 HTTPS

기본 구성은 로컬 HTTP 개발용이며 공개 배포 상태가 아닙니다. 도메인과 인증서를 준비한 뒤 다음 override를 사용합니다.

1. `nginx/tls.conf`의 `central.example.com`을 실제 도메인으로 바꿉니다.
2. `TLS_CERT_DIR`에 `fullchain.pem`, `privkey.pem`이 있는 디렉터리를 지정합니다.
3. `.env`의 `COOKIE_SECURE=true`를 설정합니다.
4. 실행합니다.

```sh
docker compose -f compose.yaml -f compose.https.yaml up -d --build
```

HTTPS override는 Nginx의 80/443 포트를 외부에 엽니다. 인증서 발급·갱신은 운영 환경에서 관리해야 합니다. 도메인·인증서가 없는 현재 로컬 실행에서는 HTTPS를 검증하지 않았습니다.

## 운영 명령

```sh
docker compose ps
docker compose logs --tail=100 backend nginx
docker compose down
```

`down`은 PostgreSQL 볼륨을 유지합니다. `down -v`는 데이터를 삭제하므로 일반 종료 명령으로 사용하지 않습니다. 비밀값·`.env`·인증서·DB·검증 이미지·outbox는 Git에서 제외합니다.

## 선택 근거

- [MyBatis Starter 호환성](https://mybatis.org/spring-boot-starter/mybatis-spring-boot-autoconfigure/): Spring Boot 4용 Starter 4 계열.
- [Spring Security CSRF](https://docs.spring.io/spring-security/reference/servlet/exploits/csrf.html): 세션 CSRF 토큰 유지 및 로그인 후 재조회.
- [Next.js 설치 요구사항](https://nextjs.org/docs/app/getting-started/installation): Node 기반 빌드·실행.

## AI 데이터 준비

왼쪽 **AI 학습** 메뉴는 이미지 수집 → 자동 라벨링 → 데이터셋 구성 → 모델 학습 → 모델 배포의 다섯 단계를 표시합니다. 수집 즉시 Gemma가 설명과 태그를 자동 저장하며 수동 프롬프트·검토 단계·SAM3 박스 생성은 사용하지 않습니다. 거의 검은 이미지는 객체 판단을 생략합니다. 수집·라벨링 완료 이미지는 좌우로 넘기며 확인하고 상세 화면에서 뒤로갈 수 있습니다. 후속 데이터셋 단계에서 기존 YOLO 박스와 Gemma 태그를 선별해 학습에 연결합니다. 자세한 내용은 [AI 파이프라인 문서](docs/AI_PIPELINE.md)를 참고하세요.

관리자 화면에서 데이터셋 구성·학습·평가·후보 등록·장치별 배포 요청을 할 수 있습니다. 실제 현장 모델 적용은 IoT의 명시적 CLI 실행이 필요하며 GPU 검증·적용·복구는 아직 현장 검증하지 않았습니다. 영상 녹화·양방향 통신, 원격 제어, 통계 대시보드, 사용자 관리는 후속 범위입니다.

## 데이터셋 구성·학습·배포 관리

AI 학습(`/ai-training`)의 데이터셋 단계에서 자동 라벨링 완료 이미지를 선택합니다. 기존 YOLO 박스와 Gemma 태그를 함께 선별해 독립 데이터셋을 만들고, 모델 학습 단계에서 학습·평가·통과 후보 등록을 요청합니다. 모델 배포 단계는 등록 후보와 장치를 연결하고 진행 결과를 보여줍니다. IoT 모델 수신·적용은 별도의 명시적 실행이 필요합니다.

데이터셋 구성 때 **자동 진행**을 선택하면 구성 → 학습 → 평가 → 후보 등록 → 배포 요청이 버튼 없이 이어집니다. 평가에서 기준 모델 이상일 때만 후보 등록으로 넘어갑니다. `.env`의 `AUTO_PIPELINE_ENABLED=true`로 새 라벨링 이미지가 쌓이면 자동 실행을 만드는 주기 트리거를 켤 수 있습니다(기본 꺼짐). 계약은 [API](docs/API.md)의 자동 진행 절을 따릅니다.

실행은 기존 `python3 scripts/dev.py start`를 사용합니다. Python 워커는 인접 학습 프로젝트의 환경과 기준 모델·데이터셋을 사용하며 Docker를 시작하지 않습니다. GPU 기본값은 `mps`; 워커 시작 환경의 `TRAINING_DEVICE`로 변경할 수 있습니다. 자세한 정책과 CLI는 [학습 가이드](docs/TRAINING_PIPELINE.md), 요청 계약은 [API](docs/API.md)를 따릅니다.

SIM 라우터 데이터 절약을 위해 감시·수집 설정은 임의 변경하지 않습니다. 운영 상태는 시점별로 달라질 수 있으므로 작업 재개 시 조회합니다. 테스트는 기존 영상의 소량 프레임과 검증 전용 장치를 사용합니다. 중앙 학습 요청은 웹캠 수집을 시작하지 않습니다.
