"""카메라 원본 장치·해상도(V35)와 이미지 크기 구간·필터를 실제 PostgreSQL로 검증한다.

- POST /api/iot/cameras/status의 선택 필드 sourceWidth·sourceHeight·sourceDevice: 저장, 같은 값 재보고 시 이력 유지,
  장치 교체 시 이력 추가, 원본 필드를 빼고 보낸 이전 IoT는 마지막 값 유지, 한쪽만 보내면 400.
- GET /api/devices/{id}/cameras/{camera}/sources: 원본 사진·수집 이미지 크기가 바뀐 구간(최근 순), 없는 카메라 404, 미인증 401.
- GET /api/training/samples의 cameraId·size 필터와 /api/training/samples/sizes 집계.

실행: python3 tests/camera_sources.py (CENTRAL_TEST_URL로 대상 서버 지정). 검증 장치는 종료 시 삭제한다.
"""
from datetime import datetime, timedelta, timezone
import unittest
import uuid

from integration import Session
from local_fixtures import DeviceFixture, sql

START = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)


def iso(t):
    return t.isoformat(timespec='microseconds')


class CameraSources(unittest.TestCase):
    def setUp(self):
        self.fixture = DeviceFixture()
        self.addCleanup(self.fixture.cleanup)
        self.iot = {'Authorization': 'Bearer ' + self.fixture.token}
        self.admin = Session(); self.assertEqual(self.admin.login()[0], 200)
        self.assertEqual(Session().call('POST', '/iot/register', {'deviceId': self.fixture.id, 'name': '일시 검증 장치'}, self.iot)[0], 200)
        self.base = f'/devices/{self.fixture.id}/cameras/usb-0'

    def status(self, **source):
        body = {'deviceId': self.fixture.id, 'cameraId': 'usb-0', 'displayName': '현장 웹캠', 'streamStatus': 'OFFLINE',
                'width': 640, 'height': 480, 'fps': 5, **source}
        return Session().call('POST', '/iot/cameras/status', body, self.iot)[0]

    def camera(self):
        return next(c for c in self.admin.call('GET', f'/devices/{self.fixture.id}/cameras')[1] if c['cameraId'] == 'usb-0')

    def test_source_report_history(self):
        self.assertEqual(self.status(), 200)
        self.assertIsNone(self.camera()['sourceWidth'])
        self.assertEqual(self.admin.call('GET', self.base + '/sources')[1]['reported'], [])

        insta = {'sourceWidth': 640, 'sourceHeight': 360, 'sourceDevice': 'Insta360 Link'}
        self.assertEqual(self.status(**insta), 200)
        self.assertEqual(self.status(**insta), 200)
        camera = self.camera()
        self.assertEqual((camera['width'], camera['height'], camera['sourceWidth'], camera['sourceHeight'], camera['sourceDevice']),
                         (640, 480, 640, 360, 'Insta360 Link'))
        self.assertEqual(len(self.admin.call('GET', self.base + '/sources')[1]['reported']), 1)

        fhd = {'sourceWidth': 640, 'sourceHeight': 480, 'sourceDevice': 'FHD Camera'}
        self.assertEqual(self.status(**fhd), 200)
        reported = self.admin.call('GET', self.base + '/sources')[1]['reported']
        self.assertEqual([(r['sourceDevice'], r['sourceWidth'], r['sourceHeight']) for r in reported],
                         [('FHD Camera', 640, 480), ('Insta360 Link', 640, 360)])

        # 원본 필드를 보내지 않는 이전 IoT는 마지막 보고값을 지우지 않는다.
        self.assertEqual(self.status(), 200)
        self.assertEqual((self.camera()['sourceDevice'], self.camera()['sourceHeight']), ('FHD Camera', 480))
        self.assertEqual(self.status(sourceWidth=640), 400)
        self.assertEqual(self.status(sourceWidth=0, sourceHeight=480), 400)
        self.assertEqual(self.status(sourceWidth=640, sourceHeight=480, sourceDevice='x' * 121), 400)

    def test_snapshot_and_sample_periods(self):
        self.assertEqual(self.status(), 200)
        # 원본 사진: 768×432 2장 → 768×576 3장 (웹캠 교체)
        body = {'batchId': str(uuid.uuid4()), 'deviceId': self.fixture.id, 'cameraId': 'usb-0', 'sessionId': str(uuid.uuid4()),
                'startedAt': iso(START), 'endedAt': iso(START + timedelta(seconds=10)), 'model': 'verify-model',
                'tracker': 'bytetrack', 'cuts': [], 'frames': [{'at': iso(START + timedelta(seconds=t)), 'objects': []} for t in range(5)]}
        self.assertEqual(Session().call('POST', '/iot/detections', body, self.iot)[0], 200)
        for t, (w, h) in enumerate([(768, 432)] * 2 + [(768, 576)] * 3):
            sql(f"INSERT INTO detection_snapshots(batch_id,captured_at,sha256,size_bytes,width,height) VALUES("
                f"'{body['batchId']}','{iso(START + timedelta(seconds=t))}','{'0' * 64}',1,{w},{h})")
        # 수집 이미지: 640×360 2장 → 640×480 1장 → 640×360 1장(다시 꽂음)
        for t, (w, h) in enumerate([(640, 360), (640, 360), (640, 480), (640, 360)]):
            sql(f"INSERT INTO training_samples(id,sha256,title,mime,width,height,device_id,camera_id,captured_at,status) VALUES("
                f"'{uuid.uuid4()}','{'1' * 64}','검증 {t}','image/jpeg',{w},{h},'{self.fixture.id}','usb-0',"
                f"'{iso(START + timedelta(minutes=t))}','LABELED')")

        sources = self.admin.call('GET', self.base + '/sources')[1]
        self.assertEqual([(p['width'], p['height'], p['count']) for p in sources['snapshots']], [(768, 576, 3), (768, 432, 2)])
        self.assertLess(sources['snapshots'][1]['lastAt'], sources['snapshots'][0]['firstAt'])
        self.assertEqual([(p['width'], p['height'], p['count']) for p in sources['samples']],
                         [(640, 360, 1), (640, 480, 1), (640, 360, 2)])

        query = f'/training/samples?deviceId={self.fixture.id}&cameraId=usb-0'
        self.assertEqual(self.admin.call('GET', query + '&size=640x360')[1]['total'], 3)
        self.assertEqual(self.admin.call('GET', query + '&size=640x480&status=LABELED')[1]['total'], 1)
        self.assertEqual(self.admin.call('GET', query)[1]['total'], 4)
        self.assertEqual(self.admin.call('GET', query + '&size=640')[0], 400)
        facets = self.admin.call('GET', f'/training/samples/sizes?deviceId={self.fixture.id}')[1]
        self.assertEqual(sorted((f['cameraId'], f['width'], f['height'], f['count']) for f in facets),
                         [('usb-0', 640, 360, 3), ('usb-0', 640, 480, 1)])

    def test_access(self):
        self.assertEqual(self.status(), 200)
        self.assertEqual(self.admin.call('GET', f'/devices/{self.fixture.id}/cameras/none-0/sources')[0], 404)
        self.assertEqual(Session().call('GET', self.base + '/sources')[0], 401)
        self.assertEqual(Session().call('GET', f'/training/samples/sizes?deviceId={self.fixture.id}')[0], 401)


if __name__ == '__main__':
    unittest.main()
