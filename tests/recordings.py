"""P1-a: 테스트 영상을 녹화 1건으로 등록하고 시각→재생 위치 변환·관리자 인증 구간 재생을 검증한다.

OpenCV가 필요하므로 `.venv/bin/python tests/recordings.py`로 실행한다.
검증 장치·녹화 행·파일은 종료 시 삭제한다.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from urllib import error, parse, request
import uuid

import cv2
import numpy as np

from integration import BASE, Session
from local_fixtures import DATA, ROOT, DeviceFixture, sql

VIDEO = ROOT.parent / 'iot-server/assets/test-video.mp4'
spec = importlib.util.spec_from_file_location('register_recording', ROOT / 'scripts/register-recording.py')
registrar = importlib.util.module_from_spec(spec); spec.loader.exec_module(registrar)
# 1차 확인한 장면(두 사람 복도·장비 작업·대화) 시각. 초 단위 [start, end) 안의 위치다.
SCENES = [15.0, 43.25, 62.0]


def fetch(opener, url, headers=None):
    try:
        target = url if isinstance(url, request.Request) else request.Request(url, headers=headers or {})
        response = opener.open(target, timeout=60)
    except error.HTTPError as exc:
        response = exc
    with response:
        return response.status, response.headers, response.read()


def frame_at(path, seconds, fps):
    """원본은 프레임 번호로, 비교 대상은 시각으로 찾는다."""
    capture = cv2.VideoCapture(str(path))
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES, int(seconds * fps))
        ok, image = capture.read()
        assert ok, seconds
        return image.astype(np.int16)
    finally:
        capture.release()


def frame_by_time(path, seconds):
    capture = cv2.VideoCapture(str(path))
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
        ok, image = capture.read()
        assert ok, seconds
        return image.astype(np.int16)
    finally:
        capture.release()


class Recordings(unittest.TestCase):
    def setUp(self):
        if not VIDEO.exists():
            self.skipTest('테스트 영상이 없습니다: ' + str(VIDEO))
        self.fixture = DeviceFixture()
        self.addCleanup(self.cleanup)
        self.admin = Session(); self.assertEqual(self.admin.login()[0], 200)
        self.camera = 'test-video'
        self.start = datetime(2026, 9, 30, 1, 0, 0, 123456, tzinfo=timezone.utc)
        self.info = registrar.probe(VIDEO)

    def cleanup(self):
        ids = sql(f"SELECT id::text FROM recordings WHERE device_id='{self.fixture.id}'").split()
        sql(f"DELETE FROM recordings WHERE device_id='{self.fixture.id}'")
        for recording in ids:
            (DATA / 'media/recordings' / (str(uuid.UUID(recording)) + '.mp4')).unlink(missing_ok=True)
        self.fixture.cleanup()

    def register(self, **overrides):
        args = {'path': VIDEO, 'device': self.fixture.id, 'camera': self.camera,
                'started_at': self.start.isoformat(), 'duration': self.info['durationSeconds']}
        args.update(overrides)
        return registrar.register(args.pop('path'), **args)

    def locate(self, session, at):
        query = parse.urlencode({'deviceId': self.fixture.id, 'cameraId': self.camera, 'at': at.isoformat()})
        return session.call('GET', '/recordings/locate?' + query)

    def test_register_locate_and_play(self):
        # 등록: 워커 토큰 필요, 없는 장치·MP4 아닌 파일 거부, 같은 파일 재등록은 같은 녹화
        unauthorized = request.Request('http://127.0.0.1:8201/internal/ai/recordings', data=b'', method='POST')
        self.assertEqual(fetch(request.build_opener(), unauthorized)[0], 401)
        self.assertEqual(self.register(device='verify-missing-device')[0], 404)
        with tempfile.NamedTemporaryFile(suffix='.mp4') as fake:
            fake.write(b'not a video file at all'); fake.flush()
            self.assertEqual(self.register(path=fake.name)[0], 400)
        code, recording = self.register()
        self.assertEqual(code, 200, recording)
        self.assertAlmostEqual(recording['durationSeconds'], self.info['durationSeconds'], places=5)
        self.assertEqual(recording['sha256'], hashlib.sha256(VIDEO.read_bytes()).hexdigest())
        self.assertEqual(self.register()[1]['id'], recording['id'])
        self.assertEqual(self.register(duration=10)[0], 409)

        # 인증: 세션 없는 조회·재생 차단
        anonymous = Session()
        self.assertEqual(self.locate(anonymous, self.start)[0], 401)
        video_url = BASE + '/api/recordings/' + recording['id'] + '/video'
        self.assertEqual(fetch(anonymous.opener, video_url)[0], 401)

        # 시각 → 재생 위치: 경계 [start, end), 마이크로초 단위 오차 확인
        self.assertEqual(self.locate(self.admin, self.start - timedelta(microseconds=1))[0], 404)
        end = self.start + timedelta(seconds=recording['durationSeconds'])
        self.assertEqual(self.locate(self.admin, end)[0], 404)
        self.assertEqual(self.locate(self.admin, self.start)[1]['offsetSeconds'], 0)
        errors = []
        for seconds in SCENES:
            code, located = self.locate(self.admin, self.start + timedelta(seconds=seconds))
            self.assertEqual(code, 200)
            self.assertEqual(located['recording']['id'], recording['id'])
            self.assertEqual(located['playbackPath'], '/api/recordings/' + recording['id'] + '/video')
            errors.append(abs(located['offsetSeconds'] - seconds))
        self.assertLess(max(errors), 1e-6, errors)

        # 재생: Range 206, 전체 파일 무결성(Next.js rewrite 경유)
        code, headers, part = fetch(self.admin.opener, video_url, {'Range': 'bytes=0-1023'})
        self.assertEqual(code, 206)
        self.assertEqual(len(part), 1024)
        self.assertTrue(headers['Content-Range'].startswith('bytes 0-1023/'))
        self.assertIn('video/mp4', headers['Content-Type'])
        code, headers, served = fetch(self.admin.opener, video_url)
        self.assertEqual(code, 200)
        self.assertIn('no-store', headers.get('Cache-Control', ''))
        self.assertEqual(hashlib.sha256(served).hexdigest(), recording['sha256'])

        # 장면: 받은 파일을 재생 위치로 찾은 프레임 = 원본의 해당 프레임, 5초 떨어진 프레임과는 다름
        with tempfile.NamedTemporaryFile(suffix='.mp4') as copy:
            copy.write(served); copy.flush()
            for seconds in SCENES:
                seen = frame_by_time(copy.name, seconds)
                same = np.abs(seen - frame_at(VIDEO, seconds, self.info['fps'])).mean()
                other = np.abs(seen - frame_at(VIDEO, seconds + 5, self.info['fps'])).mean()
                print(f'{seconds:6.2f}초 동일 프레임 차이 {same:.2f}, 5초 뒤 프레임 차이 {other:.2f}')
                self.assertLess(same, 1)
                self.assertGreater(other, 3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
