"""관리자가 올린 영상 파일·유튜브 링크에서 프레임을 추출해 중앙 수집 이미지로 전송한다."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from urllib import request

import cv2
import numpy as np

BASE = 'http://127.0.0.1:8201/internal/ai'
YOUTUBE = re.compile(r'^https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}$')
MAX_BYTES = 500 * 1024 * 1024
# 연속 프레임이 거의 같으면(64×64 흑백 평균 차이) 저장하지 않아 데이터와 라벨링 시간을 아낀다.
SIMILAR = 3.0


def multipart(fields, filename, data):
    boundary = uuid.uuid4().hex
    body = bytearray()
    for key, value in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
             'Content-Type: image/jpeg\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
    return bytes(body), 'multipart/form-data; boundary=' + boundary


def download(job, token, folder):
    if job['kind'] == 'VIDEO_FILE':
        target = folder / 'video'
        req = request.Request(BASE + '/imports/' + job['id'] + '/video', headers={'Authorization': 'Bearer ' + token})
        with request.urlopen(req, timeout=60) as response, target.open('wb') as out:
            shutil.copyfileobj(response, out, 1024 * 1024)
        return target
    if not YOUTUBE.match(job['source']):
        raise ValueError('허용하지 않는 영상 주소')
    ytdlp = Path(sys.executable).with_name('yt-dlp')
    if not ytdlp.exists():
        raise FileNotFoundError('yt-dlp')
    # 학습에는 음성이 필요 없으므로 병합(ffmpeg)이 없는 영상 스트림 하나만 받는다.
    # OpenCV가 확실히 읽는 H.264를 우선하고, 생방송·재생목록·과대 파일은 거부한다.
    subprocess.run([str(ytdlp), '--no-playlist', '--no-progress', '--quiet', '--no-warnings',
                    '-f', 'bv*[height<=720][vcodec^=avc1][protocol=https]/bv*[height<=720][protocol=https]/bv*[height<=720]',
                    '--max-filesize', '500M', '--match-filter', 'duration<=3600 & !is_live',
                    '-o', str(folder / 'video.%(ext)s'), '--', job['source']],
                   check=True, timeout=900, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    files = [p for p in folder.glob('video.*') if p.is_file() and not p.name.endswith('.part')]
    if not files:
        raise ValueError('조건에 맞는 영상 형식이 없거나 1시간을 넘는 영상')
    return files[0]


def frames(path, interval, limit):
    """interval 초 간격 위치의 프레임을 (초, JPEG) 로 돌려준다."""
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError('영상을 열 수 없습니다')
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 0
        count = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        duration = count / fps if fps > 0 and count > 0 else None
        previous, sent, position = None, 0, 0.0
        while sent < limit and (duration is None or position < duration):
            capture.set(cv2.CAP_PROP_POS_MSEC, position * 1000)
            ok, frame = capture.read()
            if not ok:
                break
            small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (64, 64)).astype(np.float32)
            if previous is None or float(np.abs(small - previous).mean()) >= SIMILAR:
                height, width = frame.shape[:2]
                if max(width, height) > 1920:
                    scale = 1920 / max(width, height)
                    frame = cv2.resize(frame, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
                ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
                if ok:
                    previous = small
                    sent += 1
                    yield position, encoded.tobytes()
            position += interval
    finally:
        capture.release()


def process(job, api, token):
    stopped, lost = threading.Event(), threading.Event()
    lease = {'leaseToken': job['leaseToken']}

    def heartbeat():
        while not stopped.wait(20):
            try:
                api('/imports/' + job['id'] + '/heartbeat', lease)
            except Exception:
                lost.set()
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='central-import-') as folder:
            path = download(job, token, Path(folder))
            if path.stat().st_size > MAX_BYTES:
                raise ValueError('영상 파일이 500MB를 넘습니다')
            sent = 0
            for position, data in frames(path, float(job['intervalSeconds']), int(job['maxFrames'])):
                if lost.is_set():
                    raise RuntimeError('lease lost')
                body, content_type = multipart({'leaseToken': job['leaseToken'], 'positionSeconds': f'{position:.3f}'},
                                               f'frame-{sent:04d}.jpg', data)
                req = request.Request(BASE + '/imports/' + job['id'] + '/frames', data=body,
                                      headers={'Authorization': 'Bearer ' + token, 'Content-Type': content_type})
                with request.urlopen(req, timeout=60) as response:
                    json.load(response)
                sent += 1
            if sent == 0:
                raise ValueError('추출한 프레임이 없습니다')
        api('/imports/' + job['id'] + '/complete', lease)
        print('영상 가져오기 완료:', job['id'], sent, '장', flush=True)
    except Exception as exc:
        reason = exc.stderr.decode(errors='replace').strip().splitlines()[-1][:200] if isinstance(
            exc, subprocess.CalledProcessError) and exc.stderr else str(exc)[:200] or type(exc).__name__
        print('영상 가져오기 실패:', job['id'], type(exc).__name__, flush=True)
        if not lost.is_set():
            try:
                api('/imports/' + job['id'] + '/fail', {**lease, 'error': '영상 처리 실패: ' + reason})
            except Exception:
                pass
    finally:
        stopped.set()
        thread.join(timeout=35)
