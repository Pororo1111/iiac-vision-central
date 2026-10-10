#!/usr/bin/env python3
"""쓰러짐 모의 IoT: 영상에 yolo26n-pose·ByteTrack과 scripts/fall_rules.py 규칙을 돌려 IoT와 같은 순서로 중앙에 보낸다.

FALL_START 확정 시 ① POST /api/iot/events(FALL_START, zoneId 생략) ② 같은 UUID를 batchId로 쓴 프레임 1개 탐지 배치
③ 그 시각의 오버레이 없는 원본 1장(긴 변 768px JPEG). FALL_END는 규칙이 종료를 낼 때만 보낸다(영상이 누운 채 끝나면 없음).
시각은 현재 UTC - 영상 길이 + 영상 위치(가상 촬영 시각)다. 장치 토큰은 환경변수 FALL_IOT_TOKEN으로 받고 출력하지 않는다.

실행: FALL_IOT_TOKEN=... .venv/bin/python scripts/simulate-fall-iot.py --device <id> --camera cam-1 영상.mp4 [...]
      --dry-run이면 판정만 출력하고 보내지 않는다.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from urllib import error, request
import uuid

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from fall_rules import FallConfig, FallDetector  # noqa: E402

POSE_MODEL = ROOT / '.artifacts/ai-assets/models/yolo26n-pose.pt'


def iso(t):
    return t.isoformat(timespec='microseconds')


def call(base, path, token, body=None, data=None, content_type='application/json'):
    payload = data if data is not None else json.dumps(body).encode()
    req = request.Request(base + path, data=payload, method='POST',
                          headers={'Authorization': 'Bearer ' + token, 'Content-Type': content_type})
    try:
        with request.urlopen(req, timeout=15) as response:
            return response.status, json.loads(response.read() or b'null')
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b'null')


def snapshot_body(batch_id, device, at, jpeg):
    boundary = 'fall-' + uuid.uuid4().hex
    fields = {'batchId': batch_id, 'deviceId': device, 'at': iso(at)}
    data = b''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
    data += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="frame.jpg"\r\n'
             'Content-Type: image/jpeg\r\n\r\n').encode() + jpeg + f'\r\n--{boundary}--\r\n'.encode()
    return data, 'multipart/form-data; boundary=' + boundary


def jpeg(image, longest=768):
    h, w = image.shape[:2]
    scale = min(1.0, longest / max(h, w))
    if scale < 1:
        image = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    return cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()


def run(video, args, cfg):
    from ultralytics import YOLO
    pose = YOLO(str(args.model))
    capture = cv2.VideoCapture(str(video))
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    length = capture.get(cv2.CAP_PROP_FRAME_COUNT) / fps
    origin = datetime.now(timezone.utc) - timedelta(seconds=length + 5)
    detector, index, sent = FallDetector(cfg), 0, []
    while True:
        ok, image = capture.read()
        if not ok:
            break
        t = index / fps
        index += 1
        result = pose.track(image, persist=True, tracker='bytetrack.yaml', conf=0.25, verbose=False)[0]
        persons = []
        if result.boxes is not None and len(result.boxes):
            ids = result.boxes.id.tolist() if result.boxes.id is not None else [None] * len(result.boxes)
            for box, tid, kp, kc in zip(result.boxes.xyxy.tolist(), ids, result.keypoints.xy.tolist(), result.keypoints.conf.tolist()):
                persons.append({'id': tid, 'box': box, 'kp': [[x, y, c] for (x, y), c in zip(kp, kc)]})
        for event in detector.update(t, persons):
            at = origin + timedelta(seconds=t)
            line = f"{video.name} {event['type']} 영상 {t:.2f}s"
            if args.dry_run:
                print(line, flush=True)
                continue
            event_id = str(uuid.uuid4())
            body = {'eventId': event_id, 'deviceId': args.device, 'cameraId': args.camera, 'type': event['type'],
                    'occurredAt': iso(at), 'objectCount': 1}
            status = call(args.base, '/api/iot/events', args.token, body)
            if event['type'] == 'FALL_START':
                h, w = image.shape[:2]
                x1, y1, x2, y2 = event['box']
                person = {'cls': 'person', 'score': 0.9, 'box': [x1 / w, y1 / h, x2 / w, y2 / h]}
                if event['trackId'] is not None:
                    person['trackId'] = f"t{int(event['trackId'])}"  # 중앙 형식 [A-Za-z0-9_-]{1,32}
                batch = {'batchId': event_id, 'deviceId': args.device, 'cameraId': args.camera, 'sessionId': str(uuid.uuid4()),
                         'startedAt': iso(at), 'endedAt': iso(at + timedelta(milliseconds=1)), 'model': args.model.stem,
                         'tracker': 'bytetrack', 'cuts': [],
                         'frames': [{'at': iso(at), 'objects': [person]}]}
                batch_status = call(args.base, '/api/iot/detections', args.token, batch)
                data, ctype = snapshot_body(event_id, args.device, at, jpeg(image))
                snap_status = call(args.base, '/api/iot/detection-snapshots', args.token, data=data, content_type=ctype)
                line += f' 이벤트 {status[0]} 배치 {batch_status[0]} 원본 {snap_status[0]} eventId={event_id}'
            else:
                line += f' 이벤트 {status[0]}'
            sent.append(event_id)
            print(line, flush=True)
    capture.release()
    return sent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('videos', nargs='+', type=Path)
    parser.add_argument('--device', required=False)
    parser.add_argument('--camera', default='cam-1')
    parser.add_argument('--base', default=os.environ.get('CENTRAL_TEST_URL', 'http://127.0.0.1:8201'))
    parser.add_argument('--model', type=Path, default=POSE_MODEL)
    parser.add_argument('--hold', type=float, default=1.0, help='누움 유지 시간(초). 검증 영상이 짧아 기본 1.0')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    args.token = os.environ.get('FALL_IOT_TOKEN', '')
    if not args.dry_run and not (args.device and args.token):
        raise SystemExit('--device와 FALL_IOT_TOKEN이 필요합니다(또는 --dry-run).')
    for video in args.videos:
        run(video, args, FallConfig(hold_seconds=args.hold))


if __name__ == '__main__':
    main()
