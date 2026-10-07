#!/usr/bin/env python3
"""P2 모의 IoT: 테스트 영상에 YOLO·ByteTrack을 실행해 10초 탐지 배치를 중앙 POST /api/iot/detections로 보낸다.
사람이 나온 배치는 원본 프레임(최대 4장, 긴 변 768px JPEG)을 POST /api/iot/detection-snapshots로 뒤이어 보낸다.

실제 IoT는 변경하지 않는다. 시각은 가상 촬영 시작 시각 + 영상 위치이며 P1-a 녹화 등록과 같은 시작 시각을 쓰면
탐지 시각으로 원본 재생 위치를 찾을 수 있다. 장면 전환(컷)을 감지하면 추적기를 초기화하고 추적 ID 앞에 장면 번호를
붙인다. 장치 토큰은 환경변수 VIDEO_IOT_TOKEN으로 받고 출력하지 않는다.

실행: VIDEO_IOT_TOKEN=... .venv/bin/python scripts/simulate-video-iot.py --device <id> --camera test-video
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from urllib import error, request
import uuid

import cv2

ROOT = Path(__file__).resolve().parents[1]
VIDEO = ROOT.parent / 'iot-server/assets/test-video.mp4'
MODEL = ROOT / '.artifacts/ai-assets/models/ppe4-rf27-local-m-20260914.pt'
CUT_DISTANCE = 0.35  # 연속 표본 프레임 HSV 히스토그램의 Bhattacharyya 거리
# 색 분포가 비슷한 장면끼리의 즉시 전환(예: 공항 송출 영상, 히스토그램 거리 0.16~0.21)은 화소 차이로 보완한다.
# 64×36 회색조 평균 절대차가 평소 p99 약 15, 즉시 전환은 약 60이었다(2026-10-04 송출 녹화 측정).
SOFT_CUT_DISTANCE = 0.15
CUT_PIXEL_DIFF = 40.0


def histogram(frame):
    small = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([small], [0, 1], None, [16, 8], [0, 180, 0, 256])
    return cv2.normalize(hist, hist)


def small_gray(frame):
    return cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (64, 36), interpolation=cv2.INTER_AREA).astype('float32')


def is_cut(previous, current):
    """previous·current는 (히스토그램, 축소 회색조). 교차 전환(디졸브)처럼 서서히 바뀌는 장면은 잡지 못한다."""
    distance = cv2.compareHist(previous[0], current[0], cv2.HISTCMP_BHATTACHARYYA)
    if distance > CUT_DISTANCE:
        return True
    return distance > SOFT_CUT_DISTANCE and float(cv2.absdiff(previous[1], current[1]).mean()) > CUT_PIXEL_DIFF


def analyze(video=VIDEO, model=MODEL, sample_fps=5.0, reset_on_cut=True, conf=0.35):
    """영상 위치(초) 기준 표본 프레임 탐지와 컷 목록을 만든다. 중앙과 무관한 순수 IoT 측 처리다."""
    from ultralytics import YOLO
    yolo = YOLO(str(model))
    capture = cv2.VideoCapture(str(video))
    fps = capture.get(cv2.CAP_PROP_FPS)
    duration = capture.get(cv2.CAP_PROP_FRAME_COUNT) / fps
    step = max(1, round(fps / sample_fps))
    frames, cuts, previous, scene, index = [], [], None, 0, 0
    while True:
        ok, image = capture.read()
        if not ok:
            break
        if index % step == 0:
            t = index / fps
            hist = (histogram(image), small_gray(image))
            if previous is not None and is_cut(previous, hist):
                cuts.append(t)
                scene += 1
                if reset_on_cut and yolo.predictor is not None:
                    # 컷 뒤 사람은 앞 장면의 사람과 같은 추적으로 이어 붙이지 않는다.
                    yolo.predictor.trackers[0].reset()
            previous = hist
            result = yolo.track(image, persist=True, conf=conf, verbose=False, tracker='bytetrack.yaml')[0]
            ids = result.boxes.id.tolist() if result.boxes.id is not None else [None] * len(result.boxes)
            objects = []
            for box, cls, score, track in zip(result.boxes.xyxyn.tolist(), result.boxes.cls.tolist(),
                                              result.boxes.conf.tolist(), ids):
                name = yolo.names[int(cls)]
                track_id = None
                if name == 'person' and track is not None:
                    track_id = f's{scene}-t{int(track)}' if reset_on_cut else f't{int(track)}'
                objects.append({'cls': name, 'score': round(score, 4),
                                'box': [round(min(max(v, 0.0), 1.0), 5) for v in box], 'trackId': track_id})
            frames.append({'t': t, 'objects': objects})
        index += 1
    capture.release()
    # decodedFrames: 실제로 읽은 프레임 수. 송출 녹화(fMP4)처럼 헤더의 프레임 수를 믿기 어려울 때 길이 계산에 쓴다.
    return {'duration': duration, 'fps': fps, 'sampleFps': fps / step, 'model': Path(model).parent.parent.name,
            'resetOnCut': reset_on_cut, 'frames': frames, 'cuts': cuts, 'decodedFrames': index}


def iso(t):
    return t.astimezone(timezone.utc).isoformat(timespec='microseconds')


def batches(analysis, device, camera, started_at, session, window=10.0):
    """[k*window, (k+1)*window) 구간별 배치. batchId는 세션·구간 번호로 정해 재전송해도 같다."""
    out, k = [], 0
    while k * window < analysis['duration']:
        start, end = k * window, min((k + 1) * window, analysis['duration'])
        base = started_at + timedelta(seconds=start)
        out.append({
            'batchId': str(uuid.uuid5(session, f'window-{k}')),
            'deviceId': device, 'cameraId': camera, 'sessionId': str(session),
            'startedAt': iso(base), 'endedAt': iso(started_at + timedelta(seconds=end)),
            'model': analysis['model'], 'tracker': 'bytetrack',
            'cuts': [iso(started_at + timedelta(seconds=c)) for c in analysis['cuts'] if start <= c < end],
            'frames': [{'at': iso(started_at + timedelta(seconds=f['t'])), 'objects': f['objects']}
                       for f in analysis['frames'] if start <= f['t'] < end]})
        k += 1
    return out


def send(batch, token, base='http://127.0.0.1:8200', attempts=5):
    """네트워크 오류·5xx는 같은 batchId로 다시 보낸다. 4xx는 재시도하지 않는다."""
    data = json.dumps(batch).encode()
    for attempt in range(attempts):
        req = request.Request(base + '/api/iot/detections', data=data, method='POST', headers={
            'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        try:
            with request.urlopen(req, timeout=30) as response:
                return response.status, json.loads(response.read())
        except error.HTTPError as exc:
            payload = exc.read()
            body = json.loads(payload) if payload else None
            if exc.code < 500 or attempt == attempts - 1:
                return exc.code, body
        except OSError:
            if attempt == attempts - 1:
                raise
        time.sleep(2 ** attempt)


SNAPSHOTS = 4       # 배치당 원본 프레임 최대 수
SNAPSHOT_SIDE = 768  # 원본 프레임 긴 변(px)


def snapshot_picks(batch, limit=SNAPSHOTS):
    """사람이 나온 프레임 중 시간상 고르게 최대 limit장. 사람이 없는 배치는 빈 목록."""
    people = [f for f in batch['frames'] if any(o['cls'] == 'person' for o in f['objects'])]
    if len(people) <= limit:
        return people
    return [people[round((len(people) - 1) * i / (limit - 1))] for i in range(limit)]


def snapshot_jpeg(capture, fps, seconds):
    """영상 위치(초)의 프레임을 긴 변 SNAPSHOT_SIDE px 이하 JPEG(품질 80)로. 테스트 영상에는 오버레이가 없다."""
    capture.set(cv2.CAP_PROP_POS_FRAMES, round(seconds * fps))
    ok, frame = capture.read()
    if not ok:
        return None
    h, w = frame.shape[:2]
    scale = min(1.0, SNAPSHOT_SIDE / max(h, w))
    if scale < 1.0:
        frame = cv2.resize(frame, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    ok, data = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return data.tobytes() if ok else None


def send_snapshot(batch, at, jpeg, token, base='http://127.0.0.1:8200', attempts=5):
    """원본 프레임 전송. 배치가 아직 없으면(409 BATCH_NOT_FOUND)·네트워크 오류·5xx·429는 같은 내용으로 다시 보낸다."""
    boundary = 'snapshot-' + uuid.uuid4().hex
    fields = {'batchId': batch['batchId'], 'deviceId': batch['deviceId'], 'at': at}
    data = b''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
    data += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="frame.jpg"\r\n'
             'Content-Type: image/jpeg\r\n\r\n').encode() + jpeg + f'\r\n--{boundary}--\r\n'.encode()
    for attempt in range(attempts):
        req = request.Request(base + '/api/iot/detection-snapshots', data=data, method='POST', headers={
            'Authorization': 'Bearer ' + token, 'Content-Type': 'multipart/form-data; boundary=' + boundary})
        try:
            with request.urlopen(req, timeout=30) as response:
                return response.status, json.loads(response.read())
        except error.HTTPError as exc:
            payload = exc.read()
            try:
                body = json.loads(payload) if payload else None
            except ValueError:
                body = None
            retry = exc.code >= 500 or exc.code == 429 or (body or {}).get('error') == 'BATCH_NOT_FOUND'
            if not retry or attempt == attempts - 1:
                return exc.code, body
        except OSError:
            if attempt == attempts - 1:
                raise
        time.sleep(2 ** attempt)


def send_snapshots(batch, token, started_at, video=VIDEO, base='http://127.0.0.1:8200'):
    """배치의 사람 프레임 원본을 영상에서 다시 읽어 보낸다. (상태 코드 목록)."""
    capture = cv2.VideoCapture(str(video))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        codes = []
        for frame in snapshot_picks(batch):
            seconds = (datetime.fromisoformat(frame['at']) - started_at).total_seconds()
            jpeg = snapshot_jpeg(capture, fps, seconds)
            if jpeg is not None:
                codes.append(send_snapshot(batch, frame['at'], jpeg, token, base)[0])
        return codes
    finally:
        capture.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True)
    parser.add_argument('--camera', default='test-video')
    parser.add_argument('--started-at', help='UTC ISO 8601 가상 촬영 시작 시각(기본: 현재)')
    parser.add_argument('--session', type=uuid.UUID, default=None)
    parser.add_argument('--sample-fps', type=float, default=5.0)
    parser.add_argument('--base', default='http://127.0.0.1:8200')
    parser.add_argument('--no-snapshots', action='store_true', help='원본 프레임을 보내지 않는다(구간 분석은 기한 뒤 녹화로 대신)')
    args = parser.parse_args()
    token = os.environ.get('VIDEO_IOT_TOKEN')
    if not token:
        raise SystemExit('VIDEO_IOT_TOKEN 환경변수가 필요합니다.')
    started = datetime.fromisoformat(args.started_at) if args.started_at else datetime.now(timezone.utc)
    analysis = analyze(sample_fps=args.sample_fps)
    results = []
    for batch in batches(analysis, args.device, args.camera, started, args.session or uuid.uuid4()):
        status, body = send(batch, token, args.base)
        snapshots = send_snapshots(batch, token, started, base=args.base) if status == 200 and not args.no_snapshots else []
        results.append({'batchId': batch['batchId'], 'startedAt': batch['startedAt'], 'frames': len(batch['frames']),
                        'cuts': len(batch['cuts']), 'status': status, 'response': body, 'snapshots': snapshots})
    print(json.dumps({'cuts': len(analysis['cuts']), 'frames': len(analysis['frames']), 'batches': results},
                     ensure_ascii=False, indent=2))
    return 0 if all(r['status'] == 200 for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
