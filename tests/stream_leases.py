"""관리자 카메라 보기의 송출 임대가 heartbeat streamRequest로 전달·연장·해제·만료되는지 실제 PostgreSQL로 검증한다.

STREAM_LEASE_REAL_WAIT=1이면 DB 시각 조작 대신 실제로 임대 길이(기본 60초)만큼 기다려 만료를 확인한다.
"""
from datetime import datetime, timezone
import os
import time
import unittest
import uuid

from integration import ENV, Session
from local_fixtures import DeviceFixture, sql


class StreamLeases(unittest.TestCase):
    def setUp(self):
        self.fixture = DeviceFixture()
        self.addCleanup(self.fixture.cleanup)
        self.iot = {'Authorization': 'Bearer ' + self.fixture.token}
        self.admin = Session(); self.assertEqual(self.admin.login()[0], 200)
        self.csrf = self.admin.csrf()
        self.assertEqual(Session().call('POST', '/iot/register', {'deviceId': self.fixture.id, 'name': '일시 검증 장치'}, self.iot)[0], 200)
        for camera, name in (('usb-0', '현장 웹캠'), ('test-video-0', '테스트 영상(시뮬레이션)')):
            body = {'deviceId': self.fixture.id, 'cameraId': camera, 'displayName': name, 'streamStatus': 'OFFLINE'}
            self.assertEqual(Session().call('POST', '/iot/cameras/status', body, self.iot)[0], 200)
        self.path = f'/devices/{self.fixture.id}/cameras/test-video-0/stream-lease'

    def beat(self):
        body = {'deviceId': self.fixture.id, 'modelName': 'best.pt', 'runtimeStatus': 'RUNNING'}
        status, data = Session().call('POST', '/iot/heartbeat', body, self.iot)
        self.assertEqual(status, 200)
        return data.get('streamRequest')

    def acquire(self, session=None, csrf=None, viewer=None, path=None):
        session = session or self.admin
        return session.call('POST', path or self.path, {'viewerId': viewer or str(uuid.uuid4())}, csrf or self.csrf)

    def release(self, viewer, session=None, csrf=None):
        return (session or self.admin).call('DELETE', f'{self.path}?viewerId={viewer}', None, csrf or self.csrf)

    def second_admin(self):
        name = 'verify-admin-' + uuid.uuid4().hex[:8]
        sql(f"INSERT INTO admins(username,password_hash,enabled) SELECT '{name}',password_hash,true FROM admins WHERE username='{ENV['ADMIN_USERNAME']}'")
        self.addCleanup(sql, f"DELETE FROM admins WHERE username='{name}'")
        other = Session()
        status = other.call('POST', '/auth/login', {'username': name, 'password': ENV['ADMIN_PASSWORD']}, other.csrf(), True)[0]
        self.assertEqual(status, 200)
        return other, other.csrf()

    def test_cameras_are_separate(self):
        cameras = self.admin.call('GET', f'/devices/{self.fixture.id}/cameras')[1]
        self.assertEqual([(c['cameraId'], c['displayName']) for c in cameras],
                         [('test-video-0', '테스트 영상(시뮬레이션)'), ('usb-0', '현장 웹캠')])

    def test_lease_in_heartbeat_and_release(self):
        self.assertIsNone(self.beat())
        viewer = str(uuid.uuid4())
        status, lease = self.acquire(viewer=viewer)
        self.assertEqual(status, 200)
        self.assertEqual(lease['cameraId'], 'test-video-0')
        self.assertTrue(lease['expiresAt'].endswith('Z'), lease['expiresAt'])
        remaining = (datetime.fromisoformat(lease['expiresAt'].replace('Z', '+00:00')) - datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(50 < remaining <= 61, remaining)
        self.assertEqual(self.beat(), lease)
        # 10초 안의 재요청은 연장하지 않고 같은 임대를 돌려준다.
        self.assertEqual(self.acquire(viewer=viewer)[1], lease)
        # 연장해도 requestId는 유지된다.
        sql(f"UPDATE stream_lease_viewers SET updated_at=now()-interval '11 seconds' WHERE device_id='{self.fixture.id}'")
        time.sleep(1.1)
        extended = self.acquire(viewer=viewer)[1]
        self.assertEqual(extended['requestId'], lease['requestId'])
        self.assertGreater(extended['expiresAt'], lease['expiresAt'])
        self.assertEqual(self.release(viewer)[0], 204)
        self.assertIsNone(self.beat())
        # 해제 뒤 새로 열면 새 requestId다.
        self.assertNotEqual(self.acquire()[1]['requestId'], lease['requestId'])

    def test_two_admins_share_lease(self):
        other, other_csrf = self.second_admin()
        first_viewer, second_viewer = str(uuid.uuid4()), str(uuid.uuid4())
        first = self.acquire(viewer=first_viewer)[1]
        second = self.acquire(other, other_csrf, second_viewer)[1]
        self.assertEqual(first['requestId'], second['requestId'])
        self.assertEqual(self.release(first_viewer)[0], 204)
        self.assertEqual(self.beat()['requestId'], first['requestId'])
        self.assertEqual(self.release(second_viewer, other, other_csrf)[0], 204)
        self.assertIsNone(self.beat())

    def test_expiry(self):
        lease = self.acquire()[1]
        self.assertEqual(self.beat()['requestId'], lease['requestId'])
        if os.environ.get('STREAM_LEASE_REAL_WAIT') == '1':
            wait = (datetime.fromisoformat(lease['expiresAt'].replace('Z', '+00:00')) - datetime.now(timezone.utc)).total_seconds()
            time.sleep(max(0, wait) + 1)
        else:
            sql(f"UPDATE stream_leases SET expires_at=now()-interval '1 second' WHERE device_id='{self.fixture.id}';"
                f"UPDATE stream_lease_viewers SET expires_at=now()-interval '1 second' WHERE device_id='{self.fixture.id}'")
        self.assertIsNone(self.beat())
        # 만료 뒤 다시 열면 새 임대(새 requestId)가 만들어진다.
        self.assertNotEqual(self.acquire()[1]['requestId'], lease['requestId'])

    def test_latest_lease_only(self):
        self.acquire(path=f'/devices/{self.fixture.id}/cameras/usb-0/stream-lease')
        time.sleep(0.05)
        latest = self.acquire()[1]
        self.assertEqual(self.beat(), latest)

    def test_rejections(self):
        viewer = {'viewerId': str(uuid.uuid4())}
        anonymous = Session()
        self.assertEqual(anonymous.call('POST', self.path, viewer, anonymous.csrf())[0], 401)
        self.assertEqual(self.admin.call('POST', self.path, viewer)[0], 403)
        # 장치 토큰으로는 관리자 API를 쓸 수 없다.
        self.assertIn(Session().call('POST', self.path, viewer, self.iot)[0], (401, 403))
        self.assertEqual(self.admin.call('POST', f'/devices/{self.fixture.id}/cameras/missing-0/stream-lease', viewer, self.csrf)[0], 404)
        self.assertEqual(self.admin.call('POST', '/devices/missing-device/cameras/usb-0/stream-lease', viewer, self.csrf)[0], 404)
        self.assertEqual(self.admin.call('POST', self.path, {'viewerId': 'not-a-uuid'}, self.csrf)[0], 400)
        self.assertEqual(self.admin.call('DELETE', self.path + '?viewerId=' + viewer['viewerId'])[0], 403)
        # 다른 장치의 heartbeat에는 이 장치의 임대가 실리지 않는다.
        self.acquire()
        other = DeviceFixture(); self.addCleanup(other.cleanup)
        headers = {'Authorization': 'Bearer ' + other.token}
        self.assertEqual(Session().call('POST', '/iot/register', {'deviceId': other.id, 'name': '일시 검증 장치'}, headers)[0], 200)
        body = {'deviceId': other.id, 'modelName': 'best.pt', 'runtimeStatus': 'RUNNING'}
        self.assertIsNone(Session().call('POST', '/iot/heartbeat', body, headers)[1]['streamRequest'])
        # 다른 장치 토큰으로 이 장치 ID의 heartbeat를 보내면 거부된다.
        self.assertEqual(Session().call('POST', '/iot/heartbeat', {**body, 'deviceId': self.fixture.id}, headers)[0], 403)


if __name__ == '__main__':
    unittest.main(verbosity=2)
