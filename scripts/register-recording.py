"""MP4 파일을 원본 녹화 1건으로 중앙에 등록한다(영상 분석·검색 P1-a).

촬영 시작 시각은 인자로 받고(테스트 영상은 가상 시각), 길이는 OpenCV로 프레임 수/fps를 읽어 계산한다.
등록은 loopback 8201의 워커 API를 사용하며 결과 JSON만 출력한다. 토큰은 출력하지 않는다.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid
from urllib import request, error

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from dev import environment


def probe(path):
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise SystemExit('영상을 열 수 없습니다: ' + str(path))
        fps = capture.get(cv2.CAP_PROP_FPS)
        frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if fps <= 0 or frames <= 0:
            raise SystemExit('fps·프레임 수를 읽을 수 없습니다.')
        return {'fps': fps, 'frames': int(frames), 'durationSeconds': round(frames / fps, 6),
                'width': int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), 'height': int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))}
    finally:
        capture.release()


def register(path, device, camera, started_at, duration, base='http://127.0.0.1:8201'):
    boundary = 'recording-' + uuid.uuid4().hex
    fields = {'deviceId': device, 'cameraId': camera, 'startedAt': started_at, 'durationSeconds': str(duration)}
    body = b''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="recording.mp4"\r\n'
             'Content-Type: video/mp4\r\n\r\n').encode() + Path(path).read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
    req = request.Request(base + '/internal/ai/recordings', data=body, method='POST', headers={
        'Authorization': 'Bearer ' + environment()['AI_WORKER_TOKEN'],
        'Content-Type': 'multipart/form-data; boundary=' + boundary})
    try:
        with request.urlopen(req, timeout=120) as response:
            return response.status, json.loads(response.read())
    except error.HTTPError as exc:
        payload = exc.read()
        return exc.code, json.loads(payload) if payload else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('--device', required=True)
    parser.add_argument('--camera', required=True)
    parser.add_argument('--started-at', help='UTC ISO 8601 촬영 시작 시각(기본: 현재)')
    args = parser.parse_args()
    started = args.started_at or datetime.now(timezone.utc).isoformat()
    info = probe(args.file)
    status, body = register(args.file, args.device, args.camera, started, info['durationSeconds'])
    print(json.dumps({'status': status, 'probe': info, 'recording': body}, ensure_ascii=False, indent=2))
    return 0 if status == 200 else 1


if __name__ == '__main__':
    raise SystemExit(main())
