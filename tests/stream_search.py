"""송출 녹화 분할이 영상 검색 입력(원본 녹화 STREAM·탐지 배치·구간 분석 작업)으로 들어가는지 실제 PostgreSQL로 검증한다.

실제 송출 대신 테스트 영상에서 MediaMTX 분할과 같은 이름의 짧은 MP4 두 개를 만들어 임시 폴더에서 색인한다.
YOLO를 실행하므로 프로젝트 .venv로 실행한다: .venv/bin/python tests/stream_search.py
"""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
import time
from pathlib import Path
import shutil
import tempfile
import unittest
from urllib import error, request
import uuid

import cv2

from local_fixtures import DATA, DeviceFixture, ROOT, sql

spec = importlib.util.spec_from_file_location('stream_indexer', ROOT / 'ai-worker/stream_indexer.py')
indexer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(indexer)


def write_clip(path, start_second, seconds, fps=5.0):
    """테스트 영상의 start_second부터 seconds 동안을 송출과 같은 5fps·640×360 MP4로 쓴다."""
    source = cv2.VideoCapture(str(indexer.video_iot.VIDEO))
    source_fps = source.get(cv2.CAP_PROP_FPS)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), fps, (640, 360))
    try:
        for i in range(round(seconds * fps)):
            source.set(cv2.CAP_PROP_POS_FRAMES, round((start_second + i / fps) * source_fps))
            ok, frame = source.read()
            if not ok:
                break
            writer.write(cv2.resize(frame, (640, 360)))
    finally:
        writer.release()
        source.release()


class StreamSearch(unittest.TestCase):
    def setUp(self):
        self.fixture = DeviceFixture()
        self.addCleanup(self.cleanup)
        token = {'Authorization': 'Bearer ' + self.fixture.token, 'Content-Type': 'application/json'}
        body = json.dumps({'deviceId': self.fixture.id, 'name': '일시 검증 장치'}).encode()
        request.urlopen(request.Request('http://127.0.0.1:8201/api/iot/register', data=body, method='POST', headers=token), timeout=10)
        self.temp = Path(tempfile.mkdtemp(prefix='stream-search-'))
        self.addCleanup(shutil.rmtree, self.temp, True)
        indexer.RECORDINGS = self.temp
        indexer.STATE = self.temp / 'state.json'
        self.camera_dir = self.temp / 'devices' / self.fixture.id / 'cam-0'
        self.camera_dir.mkdir(parents=True)
        # 분할 이름은 중앙 지역 시각이다. 두 번째 분할은 첫 분할 시작 10초 뒤에 시작한다(첫 분할은 12초 분량).
        self.start = datetime.now(timezone.utc).replace(microsecond=123456) - timedelta(minutes=10)
        names = [(self.start + timedelta(seconds=s)).astimezone().strftime('%Y-%m-%d_%H-%M-%S-%f') + '.mp4' for s in (0, 10)]
        write_clip(self.camera_dir / names[0], 40, 12)
        write_clip(self.camera_dir / names[1], 52, 12)
        for path in self.camera_dir.iterdir():  # 다 쓰인 분할처럼 수정 시각을 1분 전으로 둔다.
            os.utime(path, (time.time() - 60, time.time() - 60))

    def cleanup(self):
        ids = sql(f"SELECT id::text FROM recordings WHERE device_id='{self.fixture.id}'").split()
        self.fixture.cleanup()
        for recording in ids:
            (DATA / 'media/recordings' / (recording + '.mp4')).unlink(missing_ok=True)

    def test_worker_endpoint_requires_token(self):
        req = request.Request('http://127.0.0.1:8201/internal/ai/detections', data=b'{}', method='POST',
                              headers={'Content-Type': 'application/json'})
        with self.assertRaises(error.HTTPError) as caught:
            request.urlopen(req, timeout=10)
        self.assertEqual(caught.exception.code, 401)
        # 장치 토큰으로는 워커 API를 쓸 수 없다.
        req.add_header('Authorization', 'Bearer ' + self.fixture.token)
        with self.assertRaises(error.HTTPError) as caught:
            request.urlopen(req, timeout=10)
        self.assertEqual(caught.exception.code, 401)

    def test_segments_become_searchable_inputs(self):
        state = {}
        indexer.run_once(state)
        self.assertEqual(sorted(v['status'] for v in state.values()), ['done', 'done'], state)
        rows = sql(f"SELECT source,extract(epoch FROM started_at)::numeric(16,3),"
                   f"extract(epoch FROM ended_at-started_at)::numeric(8,3) FROM recordings "
                   f"WHERE device_id='{self.fixture.id}' ORDER BY started_at").split()
        expected_start = round(self.start.timestamp(), 3)
        # 첫 분할은 다음 분할 시작에서 잘린다(12초 → 10초). 마지막 분할은 프레임 수 기준 12초다.
        self.assertEqual(rows, [f'STREAM|{expected_start:.3f}|10.000', f'STREAM|{expected_start + 10:.3f}|12.000'])
        batches = sql(f"SELECT count(*),count(DISTINCT batch_id) FROM detection_batches WHERE device_id='{self.fixture.id}'").strip()
        observations = sql(f"SELECT count(*) FROM observations WHERE device_id='{self.fixture.id}'").strip()
        self.assertEqual(batches, '3|3')  # 10초 분할 1개 + 12초 분할 2개(10초+2초)
        self.assertEqual(observations, '3')
        # 다시 처리해도(기록을 잃은 경우) 녹화·배치가 늘지 않는다.
        indexer.run_once({})
        self.assertEqual(sql(f"SELECT count(*) FROM recordings WHERE device_id='{self.fixture.id}'").strip(), '2')
        self.assertEqual(sql(f"SELECT count(*) FROM detection_batches WHERE device_id='{self.fixture.id}'").strip(), '3')
        # 구간 분석 작업은 분할 안 시각을 덮는 녹화를 찾을 수 있다.
        covered = sql(f"""SELECT count(*) FROM observations o WHERE o.device_id='{self.fixture.id}' AND EXISTS(
            SELECT 1 FROM recordings r WHERE r.device_id=o.device_id AND r.camera_id=o.camera_id
              AND r.started_at<=o.started_at AND r.ended_at>o.started_at)""").strip()
        self.assertEqual(covered, '3')

    def test_last_segment_waits_while_settling(self):
        newest = sorted(self.camera_dir.iterdir())[-1]
        newest.touch()  # 방금 쓰인 마지막 분할은 송출 중일 수 있어 아직 처리하지 않는다.
        state = {}
        indexer.run_once(state)
        self.assertEqual([Path(k).name for k in state], [sorted(self.camera_dir.iterdir())[0].name])


if __name__ == '__main__':
    unittest.main(verbosity=2)
