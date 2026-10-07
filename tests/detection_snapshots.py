"""원본 프레임(오버레이 없는 스냅샷) 계약과 구간 분석 대기 규칙을 실제 PostgreSQL·실행 중인 observer로 검증한다.

- POST /api/iot/detection-snapshots: 저장·재전송 중복·충돌·배치 없음·배치 밖 시각·장수 초과·형식·크기·장치 불일치.
- 사람이 없는 배치는 구간 분석 없이 SKIPPED(no_person).
- 사람이 나온 장치 배치는 원본 프레임이 모두 올 때까지 기다리고, 오면 녹화 없이도 원본 프레임으로 분석한다
  (검은 이미지라 Gemma 호출 없이 dark로 생략된다).
- 기한이 지나도 원본 프레임·녹화가 모두 없으면 계속 기다린다.
- 색인 워커용 iot-active.

실행: python3 tests/detection_snapshots.py (Pillow 필요, 없으면 .venv/bin/python). 검증 장치·파일은 종료 시 삭제한다.
"""
from datetime import datetime, timedelta, timezone
import io
import json
import shutil
import time
import unittest
from urllib import error, request
import uuid

from PIL import Image

from integration import BASE, ENV, Session
from local_fixtures import DATA, DeviceFixture, sql

START = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=10)


def iso(t):
    return t.isoformat(timespec='microseconds')


def jpeg(color='black', size=(64, 64), fmt='JPEG'):
    out = io.BytesIO(); Image.new('RGB', size, color).save(out, fmt); return out.getvalue()


def person(track='t1'):
    return {'cls': 'person', 'score': 0.9, 'box': [0.1, 0.1, 0.4, 0.9], 'trackId': track}


def raw(method, url, data=None, headers=None):
    req = request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with request.urlopen(req, timeout=10) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None
    except error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body) if body else None
        except ValueError:
            return exc.code, None


class Snapshots(unittest.TestCase):
    def setUp(self):
        self.fixture = DeviceFixture()
        self.other = DeviceFixture()
        self.batches = []
        self.addCleanup(self.cleanup)
        for f in (self.fixture, self.other):
            self.assertEqual(Session().call('POST', '/iot/register', {'deviceId': f.id, 'name': '일시 검증 장치'},
                                            {'Authorization': 'Bearer ' + f.token})[0], 200)

    def cleanup(self):
        for f in (self.fixture, self.other):
            f.cleanup()
        for batch in self.batches:
            shutil.rmtree(DATA / 'media/detection-snapshots' / batch, ignore_errors=True)
            (DATA / 'media/observations' / (batch + '.jpg')).unlink(missing_ok=True)

    def batch(self, frames, fixture=None, camera='cam-1', offset=0):
        fixture = fixture or self.fixture
        start = START + timedelta(seconds=offset)
        body = {'batchId': str(uuid.uuid4()), 'deviceId': fixture.id, 'cameraId': camera, 'sessionId': str(uuid.uuid4()),
                'startedAt': iso(start), 'endedAt': iso(start + timedelta(seconds=10)), 'model': 'verify-model',
                'tracker': 'bytetrack', 'cuts': [],
                'frames': [{'at': iso(start + timedelta(seconds=t)), 'objects': objects} for t, objects in frames]}
        code, result = Session().call('POST', '/iot/detections', body, {'Authorization': 'Bearer ' + fixture.token})
        self.assertEqual(code, 200, result)
        self.batches.append(body['batchId'])
        return body

    def snapshot(self, batch_id, at, content, fixture=None, device=None, token=None, omit=()):
        fixture = fixture or self.fixture
        boundary = 'snap-' + uuid.uuid4().hex
        fields = {'batchId': batch_id, 'deviceId': device or fixture.id, 'at': at}
        data = b''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                        for k, v in fields.items() if k not in omit)
        if 'file' not in omit:
            data += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="f.jpg"\r\n'
                     'Content-Type: image/jpeg\r\n\r\n').encode() + content + b'\r\n'
        data += f'--{boundary}--\r\n'.encode()
        headers = {'Content-Type': 'multipart/form-data; boundary=' + boundary}
        if token != '':
            headers['Authorization'] = 'Bearer ' + (token or fixture.token)
        return raw('POST', BASE + '/api/iot/detection-snapshots', data, headers)

    def observation(self, batch_id):
        return sql(f"""SELECT status,coalesce(skip_reason,''),snapshot_expected,snapshot_due IS NOT NULL,coalesce(frame_source,'')
            FROM observations WHERE batch_id='{batch_id}'""").strip().split('|')

    def test_contract(self):
        body = self.batch([(t, [person()]) for t in (1, 2, 3, 4, 5)] + [(6, [])])
        bid, ats = body['batchId'], [f['at'] for f in body['frames']]
        black, gray = jpeg(), jpeg('#808080')

        self.assertEqual(self.snapshot(bid, ats[0], black), (200, {'accepted': True, 'duplicate': False}))
        self.assertEqual(self.snapshot(bid, ats[0], black), (200, {'accepted': True, 'duplicate': True}))
        # 같은 시각을 다른 오프셋 표기로 보내도 같은 프레임이다.
        local = datetime.fromisoformat(ats[0]).astimezone(timezone(timedelta(hours=9))).isoformat(timespec='microseconds')
        self.assertEqual(self.snapshot(bid, local, black)[1]['duplicate'], True)
        self.assertEqual(self.snapshot(bid, ats[0], gray), (409, {'error': 'SNAPSHOT_CONFLICT'}))
        self.assertEqual(self.snapshot(bid, iso(START + timedelta(seconds=1.5)), black), (400, {'error': 'SNAPSHOT_NOT_IN_BATCH'}))
        self.assertEqual(self.snapshot(str(uuid.uuid4()), ats[1], black), (409, {'error': 'BATCH_NOT_FOUND'}))
        # 다른 장치의 배치는 존재를 드러내지 않는다.
        self.assertEqual(self.snapshot(bid, ats[1], black, fixture=self.other), (409, {'error': 'BATCH_NOT_FOUND'}))
        self.assertEqual(self.snapshot(bid, ats[1], black, device=self.other.id), (403, {'error': 'DEVICE_MISMATCH'}))
        self.assertEqual(self.snapshot(bid, ats[1], black, token='')[0], 401)
        self.assertEqual(self.snapshot(bid, ats[1], black, token='x' * 64)[0], 401)
        self.assertEqual(self.snapshot(bid, ats[1], jpeg(fmt='PNG')), (400, {'error': 'INVALID_IMAGE'}))
        self.assertEqual(self.snapshot(bid, ats[1], b'not an image'), (400, {'error': 'INVALID_IMAGE'}))
        self.assertEqual(self.snapshot(bid, ats[1], jpeg(size=(16, 16))), (400, {'error': 'INVALID_IMAGE'}))
        self.assertEqual(self.snapshot(bid, ats[1], b'\xff' * (1024 * 1024 + 1)), (413, {'error': 'FILE_TOO_LARGE'}))
        self.assertEqual(self.snapshot(bid, ats[1], black, omit=('at',)), (400, {'error': 'INVALID_REQUEST'}))
        self.assertEqual(self.snapshot(bid, ats[1], black, omit=('file',)), (400, {'error': 'INVALID_REQUEST'}))
        self.assertEqual(self.snapshot('not-uuid', ats[1], black), (400, {'error': 'INVALID_REQUEST'}))
        self.assertEqual(self.snapshot(bid, 'yesterday', black), (400, {'error': 'INVALID_REQUEST'}))

        for at in ats[1:4]:
            self.assertEqual(self.snapshot(bid, at, black)[0], 200)
        # 사람 프레임 5개 중 4장까지만 받는다. 사람이 없는 프레임도 시각만 맞으면 받지만 장수에 포함된다.
        self.assertEqual(self.snapshot(bid, ats[4], black), (400, {'error': 'TOO_MANY_SNAPSHOTS'}))
        self.assertEqual(sql(f"SELECT count(*),sum(size_bytes)={len(black) * 4},min(width) FROM detection_snapshots WHERE batch_id='{bid}'").strip(),
                         '4|t|64')
        files = sorted(p.name for p in (DATA / 'media/detection-snapshots' / bid).iterdir())
        self.assertEqual(len(files), 4)
        self.assertEqual((DATA / 'media/detection-snapshots' / bid / files[0]).read_bytes(), black)

    def test_queue_rules(self):
        empty = self.batch([(1, []), (2, [{'cls': 'hard_hat', 'score': 0.8, 'box': [0.1, 0.1, 0.2, 0.2]}])])
        self.assertEqual(self.observation(empty['batchId']), ['SKIPPED', 'no_person', '0', 'f', ''])

        # 사람 프레임 2개 → 원본 프레임 2장을 기다린다. 녹화가 없어도 원본 프레임이 모두 오면 분석한다.
        waiting = self.batch([(1, [person()]), (2, []), (3, [person()])], offset=20)
        bid, ats = waiting['batchId'], [waiting['frames'][0]['at'], waiting['frames'][2]['at']]
        self.assertEqual(self.observation(bid), ['QUEUED', '', '2', 't', ''])
        self.assertEqual(self.snapshot(bid, ats[0], jpeg())[0], 200)
        time.sleep(4)
        self.assertEqual(self.observation(bid)[0], 'QUEUED', '원본 프레임이 덜 왔는데 분석을 시작했다')
        self.assertEqual(self.snapshot(bid, ats[1], jpeg())[0], 200)
        began = time.monotonic()
        while time.monotonic() - began < 90 and self.observation(bid)[0] in ('QUEUED', 'RUNNING'):
            time.sleep(1)
        # 검은 원본 프레임이라 Gemma 호출 없이 dark로 생략된다. 원본 프레임으로 판정했음을 남긴다.
        self.assertEqual(self.observation(bid), ['SKIPPED', 'dark', '2', 't', 'SNAPSHOT'])

        # 기한이 지나도 원본 프레임·녹화가 모두 없으면 가져가지 않는다.
        stale = self.batch([(1, [person()])], offset=40)
        sql(f"UPDATE observations SET snapshot_due=now()-interval '1 minute' WHERE batch_id='{stale['batchId']}'")
        time.sleep(4)
        self.assertEqual(self.observation(stale['batchId'])[0], 'QUEUED')

    def test_event_moment(self):
        # 침입 감지 순간 전송: 그 프레임 하나의 배치([at, at+1ms))와 원본 1장 → 바로 원본으로 분석한다.
        at = START + timedelta(minutes=2, seconds=3.12)
        body = {'batchId': str(uuid.uuid4()), 'deviceId': self.fixture.id, 'cameraId': 'cam-1', 'sessionId': str(uuid.uuid4()),
                'startedAt': iso(at), 'endedAt': iso(at + timedelta(milliseconds=1)), 'model': 'verify-model',
                'tracker': 'bytetrack', 'cuts': [], 'frames': [{'at': iso(at), 'objects': [person('s1t4')]}]}
        code, result = Session().call('POST', '/iot/detections', body, {'Authorization': 'Bearer ' + self.fixture.token})
        self.assertEqual(code, 200, result)
        self.batches.append(body['batchId'])
        bid = body['batchId']
        self.assertEqual(self.observation(bid)[:3], ['QUEUED', '', '1'])
        self.assertEqual(self.snapshot(bid, iso(at), jpeg())[0], 200)
        began = time.monotonic()
        while time.monotonic() - began < 90 and self.observation(bid)[0] in ('QUEUED', 'RUNNING'):
            time.sleep(1)
        self.assertEqual(self.observation(bid), ['SKIPPED', 'dark', '1', 't', 'SNAPSHOT'])

        # 쓰러진 사람 검색 조건(fallen=true)은 집계 fallen > 0인 구간만 찾는다.
        sql(f"""UPDATE observations SET status='COMPLETED',skip_reason=NULL,summary='작업자가 바닥에 쓰러져 있다',people=1,fallen=1,
            vest_present=1,vest_absent=0,vest_unknown=0,helmet_present=1,helmet_absent=0,helmet_unknown=0,workers_no_helmet=0
            WHERE batch_id='{bid}'""")
        admin = Session(); self.assertEqual(admin.login()[0], 200)
        code, found = admin.call('GET', f'/observations/search?deviceId={self.fixture.id}&fallen=true')
        self.assertEqual((code, [s['batchId'] for s in found], found[0]['counts']['fallen']), (200, [bid], 1))
        sql(f"UPDATE observations SET fallen=0 WHERE batch_id='{bid}'")
        self.assertEqual(admin.call('GET', f'/observations/search?deviceId={self.fixture.id}&fallen=true')[1], [])
        self.assertEqual(len(admin.call('GET', f'/observations/search?deviceId={self.fixture.id}')[1]), 1)

    def test_iot_active(self):
        def active(device, camera):
            return raw('GET', f'http://127.0.0.1:8201/internal/ai/detections/iot-active?deviceId={device}&cameraId={camera}',
                       headers={'Authorization': 'Bearer ' + ENV['AI_WORKER_TOKEN']})
        self.assertEqual(active(self.fixture.id, 'cam-1'), (200, {'active': False}))
        self.batch([(1, [])])
        self.assertEqual(active(self.fixture.id, 'cam-1'), (200, {'active': True}))
        self.assertEqual(active(self.fixture.id, 'cam-2'), (200, {'active': False}))
        self.assertEqual(sql(f"SELECT source FROM detection_batches WHERE device_id='{self.fixture.id}'").strip(), 'IOT')
        self.assertEqual(raw('GET', f'http://127.0.0.1:8201/internal/ai/detections/iot-active?deviceId={self.fixture.id}&cameraId=cam-1')[0], 401)


if __name__ == '__main__':
    unittest.main(verbosity=2)
