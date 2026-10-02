#!/usr/bin/env python3
"""P2 모의 IoT: 테스트 영상에 YOLO·ByteTrack을 실행해 10초 탐지 배치를 중앙 POST /api/iot/detections로 보낸다.

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


def histogram(frame):
    small = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([small], [0, 1], None, [16, 8], [0, 180, 0, 256])
    return cv2.normalize(hist, hist)


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
            hist = histogram(image)
            if previous is not None and cv2.compareHist(previous, hist, cv2.HISTCMP_BHATTACHARYYA) > CUT_DISTANCE:
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
    return {'duration': duration, 'fps': fps, 'sampleFps': fps / step, 'model': Path(model).parent.parent.name,
            'resetOnCut': reset_on_cut, 'frames': frames, 'cuts': cuts}


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True)
    parser.add_argument('--camera', default='test-video')
    parser.add_argument('--started-at', help='UTC ISO 8601 가상 촬영 시작 시각(기본: 현재)')
    parser.add_argument('--session', type=uuid.UUID, default=None)
    parser.add_argument('--sample-fps', type=float, default=5.0)
    parser.add_argument('--base', default='http://127.0.0.1:8200')
    args = parser.parse_args()
    token = os.environ.get('VIDEO_IOT_TOKEN')
    if not token:
        raise SystemExit('VIDEO_IOT_TOKEN 환경변수가 필요합니다.')
    started = datetime.fromisoformat(args.started_at) if args.started_at else datetime.now(timezone.utc)
    analysis = analyze(sample_fps=args.sample_fps)
    results = []
    for batch in batches(analysis, args.device, args.camera, started, args.session or uuid.uuid4()):
        status, body = send(batch, token, args.base)
        results.append({'batchId': batch['batchId'], 'startedAt': batch['startedAt'], 'frames': len(batch['frames']),
                        'cuts': len(batch['cuts']), 'status': status, 'response': body})
    print(json.dumps({'cuts': len(analysis['cuts']), 'frames': len(analysis['frames']), 'batches': results},
                     ensure_ascii=False, indent=2))
    return 0 if all(r['status'] == 200 for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
