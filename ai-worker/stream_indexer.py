#!/usr/bin/env python3
"""장치 송출 녹화를 영상 검색에 넣는 호스트 워커.

MediaMTX가 `devices/{deviceId}/{cameraId}` 송출을 1분 단위 fMP4로 녹화하면(media/mediamtx.yml), 완료된 분할 파일마다
1) 중앙 원본 녹화(source=STREAM)로 등록하고 2) YOLO·ByteTrack으로 10초 탐지 배치를 만들어 워커 API로 보낸다.
탐지 배치가 저장되면 중앙이 같은 트랜잭션에서 구간 분석(Gemma) 작업을 등록하고 observer.py가 처리한다.

- 장치가 탐지 배치(`POST /api/iot/detections`)를 보내는 카메라(최근 24시간 안에 장치 배치가 있음)는 녹화만 등록하고
  다시 탐지하지 않는다. 같은 시간대에 탐지가 두 벌 생기지 않게 하기 위해서다. 그 밖의 카메라는 중앙이 송출 영상에서 직접 탐지한다.
- 송출에는 IoT가 그린 탐지 박스·라벨이 들어 있다. 그 위에서 다시 탐지·판정하므로 결과가 깨끗한 원본보다 나쁠 수 있다.
- 녹화 시작 시각은 MediaMTX 분할 시작 시각(중앙 수신 기준)에서 STREAM_LATENCY_SECONDS(송출 지연, 기본 1.6초)를 뺀 값이다.
  장치 배치 시각(촬영 시각)과 재생 위치를 맞추는 보정이다(2026-10-05 침입 3건 측정 1.2~2.0초).

처리 기록은 Git 제외 `.artifacts/local-dev/stream-indexer.json`에 둔다. 다시 처리해도 녹화 등록과 batchId가 같아 중복 저장되지 않는다.
"""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback
from urllib import error, request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from dev import DATA, environment  # noqa: E402

_spec = importlib.util.spec_from_file_location('simulate_video_iot', ROOT / 'scripts/simulate-video-iot.py')
video_iot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(video_iot)

RECORDINGS = DATA / 'stream-recordings'
STATE = DATA / 'stream-indexer.json'
BASE = os.environ.get('CENTRAL_INTERNAL_URL', 'http://127.0.0.1:8201')
MEDIA_API = os.environ.get('MEDIA_API_URL', 'http://127.0.0.1:9997').rstrip('/')
MODEL = Path(os.environ.get('STREAM_INDEX_MODEL', str(video_iot.MODEL)))
ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
NAME = re.compile(r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-\d{6}\.mp4$')
INTERVAL = 10       # 분할 파일 확인 주기(초)
SETTLE = 15         # 송출이 끝난 뒤 마지막 분할이 다 쓰였다고 볼 대기 시간(초)
MIN_SECONDS = 1.0   # 이보다 짧은 분할은 건너뛴다
# 송출 지연(초). 2026-10-05 측정 중앙값 1.6초(application.yaml stream-latency-ms와 같은 값).
LATENCY = float(os.environ.get('STREAM_LATENCY_SECONDS', '1.6'))
MAX_ATTEMPTS = 3


def log(message, **fields):
    print(json.dumps({'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'message': message, **fields},
                     ensure_ascii=False), flush=True)


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state):
    temp = STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    temp.replace(STATE)


def started_at(path):
    """파일 이름(MediaMTX recordPath의 %Y-%m-%d_%H-%M-%S-%f, 중앙 서버 지역 시각)을 UTC로 바꾸고 송출 지연을 뺀다."""
    return datetime.strptime(path.stem, '%Y-%m-%d_%H-%M-%S-%f').astimezone().astimezone(timezone.utc) - timedelta(seconds=LATENCY)


def stream_ready(device, camera):
    try:
        with request.urlopen(f'{MEDIA_API}/v3/paths/get/devices/{device}/{camera}', timeout=2) as response:
            return bool(json.loads(response.read()).get('ready'))
    except error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise
    # 연결 실패(OSError)는 호출자에게 넘겨 이번 주기를 건너뛴다. 송출 중인 마지막 분할을 완료로 오인하지 않게 한다.


def pending_segments():
    """완료된 분할 (장치, 카메라, 파일, 다음 분할 시작) 목록. 마지막 분할은 송출이 끝나고 SETTLE초가 지나야 완료로 본다."""
    out = []
    for camera_dir in sorted(RECORDINGS.glob('devices/*/*')):
        device, camera = camera_dir.parent.name, camera_dir.name
        if not (ID.match(device) and ID.match(camera)):
            continue
        files = sorted(p for p in camera_dir.iterdir() if p.is_file() and NAME.match(p.name))
        if not files:
            continue
        last_done = time.time() - files[-1].stat().st_mtime > SETTLE and not stream_ready(device, camera)
        for i, path in enumerate(files):
            if i == len(files) - 1 and not last_done:
                break
            out.append((device, camera, path, started_at(files[i + 1]) if i + 1 < len(files) else None))
    return out


def iot_active(device, camera):
    """장치가 직접 탐지 배치를 보내는 카메라인지 중앙에 묻는다."""
    req = request.Request(f'{BASE}/internal/ai/detections/iot-active?deviceId={device}&cameraId={camera}',
                          headers={'Authorization': 'Bearer ' + environment()['AI_WORKER_TOKEN']})
    with request.urlopen(req, timeout=10) as response:
        return bool(json.loads(response.read())['active'])


def decoded_length(path):
    """YOLO 없이 프레임만 세어 (fps, 디코딩된 프레임 수)를 구한다. fMP4의 프레임 수 메타데이터는 믿지 않는다."""
    import cv2
    capture = cv2.VideoCapture(str(path))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        count = 0
        while capture.grab():
            count += 1
        return fps, count
    finally:
        capture.release()


def post(path, body, content_type):
    req = request.Request(BASE + path, data=body, method='POST', headers={
        'Authorization': 'Bearer ' + environment()['AI_WORKER_TOKEN'], 'Content-Type': content_type})
    try:
        with request.urlopen(req, timeout=120) as response:
            return response.status, json.loads(response.read())
    except error.HTTPError as exc:
        payload = exc.read()
        try:
            return exc.code, json.loads(payload) if payload else None
        except ValueError:
            return exc.code, None


def register_recording(path, device, camera, start, duration):
    boundary = 'stream-' + uuid.uuid4().hex
    fields = {'deviceId': device, 'cameraId': camera, 'startedAt': video_iot.iso(start),
              'durationSeconds': f'{duration:.6f}', 'source': 'STREAM'}
    body = b''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                    for k, v in fields.items())
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="segment.mp4"\r\n'
             'Content-Type: video/mp4\r\n\r\n').encode() + path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
    return post('/internal/ai/recordings', body, 'multipart/form-data; boundary=' + boundary)


def index_segment(device, camera, path, next_start):
    start = started_at(path)
    detect = not iot_active(device, camera)
    if detect:
        analysis = video_iot.analyze(video=path, model=MODEL)
        fps, decoded = analysis['fps'], analysis['decodedFrames']
    else:
        fps, decoded = decoded_length(path)
    if fps <= 0 or decoded == 0:
        return {'status': 'empty'}
    duration = decoded / fps
    if next_start is not None:
        # 송출 시작 직후 프레임이 몰려 들어오면 프레임 수 기준 길이가 다음 분할 시작을 넘는다. 녹화끼리 겹치지 않게 자른다.
        duration = min(duration, (next_start - start).total_seconds())
    if duration < MIN_SECONDS:
        return {'status': 'empty', 'durationSeconds': duration}
    if not detect:
        status, recording = register_recording(path, device, camera, start, duration)
        if status != 200:
            raise RuntimeError(f'녹화 등록 실패 {status}: {recording}')
        return {'status': 'done', 'recordingId': recording['id'], 'durationSeconds': round(duration, 3), 'detection': 'iot'}
    analysis['duration'] = duration
    analysis['frames'] = [f for f in analysis['frames'] if f['t'] < duration]
    analysis['cuts'] = [c for c in analysis['cuts'] if c < duration]

    status, recording = register_recording(path, device, camera, start, duration)
    if status != 200:
        raise RuntimeError(f'녹화 등록 실패 {status}: {recording}')
    session = uuid.uuid5(uuid.NAMESPACE_URL, f'central-stream:{device}/{camera}/{path.name}')
    sent = 0
    for batch in video_iot.batches(analysis, device, camera, start, session):
        if not batch['frames']:
            continue
        status, body = post('/internal/ai/detections', json.dumps(batch).encode(), 'application/json')
        if status != 200:
            raise RuntimeError(f'탐지 배치 실패 {status}: {body}')
        sent += 1
    persons = sum(1 for f in analysis['frames'] for o in f['objects'] if o['cls'] == 'person')
    return {'status': 'done', 'recordingId': recording['id'], 'durationSeconds': round(duration, 3), 'detection': 'central',
            'batches': sent, 'cuts': len(analysis['cuts']), 'personDetections': persons}


def run_once(state):
    try:
        segments = pending_segments()
    except OSError as exc:
        log('MediaMTX 상태 확인 실패, 다음 주기에 다시 확인', error=str(exc))
        return
    for device, camera, path, next_start in segments:
        key = str(path.relative_to(RECORDINGS))
        entry = state.get(key, {})
        if entry.get('status') in ('done', 'empty') or entry.get('attempts', 0) >= MAX_ATTEMPTS:
            continue
        began = time.time()
        try:
            result = index_segment(device, camera, path, next_start)
        except Exception as exc:  # 한 분할의 실패가 다른 분할 처리를 막지 않게 기록하고 넘어간다.
            attempts = entry.get('attempts', 0) + 1
            state[key] = {'status': 'failed' if attempts >= MAX_ATTEMPTS else 'retry', 'attempts': attempts,
                          'error': str(exc)[:300]}
            log('분할 처리 실패', segment=key, attempts=attempts, error=str(exc)[:300])
            traceback.print_exc()
        else:
            state[key] = {**result, 'attempts': entry.get('attempts', 0) + 1,
                          'elapsedSeconds': round(time.time() - began, 1)}
            log('분할 처리', segment=key, **state[key])
        save_state(state)
    # MediaMTX가 지운(recordDeleteAfter) 분할의 처리 기록은 정리한다.
    for key in [k for k in state if not (RECORDINGS / k).exists()]:
        del state[key]
        save_state(state)


def main():
    log('송출 녹화 색인 시작', recordings=str(RECORDINGS), model=MODEL.name)
    state = load_state()
    while True:
        run_once(state)
        time.sleep(INTERVAL)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'once':
        run_once(load_state())
    else:
        main()
