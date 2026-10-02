"""중앙 감시 중지·재개 명령이 heartbeat 응답으로 IoT에 전달되는지 실제 PostgreSQL로 검증한다."""
import unittest

from integration import Session
from local_fixtures import DeviceFixture


class Monitoring(unittest.TestCase):
    def setUp(self):
        self.fixture = DeviceFixture()
        self.addCleanup(self.fixture.cleanup)
        self.iot = {'Authorization': 'Bearer ' + self.fixture.token}
        self.admin = Session(); self.assertEqual(self.admin.login()[0], 200)
        self.csrf = self.admin.csrf()
        self.assertEqual(Session().call('POST', '/iot/register', {'deviceId': self.fixture.id, 'name': '일시 검증 장치'}, self.iot)[0], 200)

    def beat(self, status='RUNNING'):
        return Session().call('POST', '/iot/heartbeat', {'deviceId': self.fixture.id, 'modelName': 'best.pt', 'runtimeStatus': status}, self.iot)

    def test_command_flow(self):
        self.assertEqual(self.beat(), (200, {'accepted': True, 'monitoring': None, 'deployment': None, 'restart': None, 'logRequest': None}))
        path = f'/devices/{self.fixture.id}/monitoring'
        self.assertEqual(self.admin.call('POST', path, {'action': 'PAUSE'})[0], 403)
        self.assertEqual(self.admin.call('POST', path, {'action': 'STOP'}, self.csrf)[0], 400)
        self.assertEqual(self.admin.call('POST', '/devices/missing-device/monitoring', {'action': 'PAUSE'}, self.csrf), (404, {'error': 'DEVICE_NOT_FOUND'}))
        self.assertEqual(Session().call('POST', path, {'action': 'PAUSE'}, self.iot)[0], 403)
        status, device = self.admin.call('POST', path, {'action': 'PAUSE'}, self.csrf)
        self.assertEqual((status, device['monitoringAction']), (200, 'PAUSE'))
        first = self.beat()[1]['monitoring']
        self.assertEqual(first['action'], 'PAUSE')
        self.assertEqual(len(first['commandId']), 36)
        # 같은 명령은 반영 뒤에도 같은 commandId로 반복 전달되어 IoT가 중복 적용하지 않는다.
        self.assertEqual(self.beat('STOPPED')[1]['monitoring']['commandId'], first['commandId'])
        listed = next(d for d in self.admin.call('GET', '/devices')[1] if d['deviceId'] == self.fixture.id)
        self.assertEqual((listed['runtimeStatus'], listed['monitoringAction']), ('STOPPED', 'PAUSE'))
        self.admin.call('POST', path, {'action': 'RESUME'}, self.csrf)
        second = self.beat('STOPPED')[1]['monitoring']
        self.assertEqual(second['action'], 'RESUME')
        self.assertNotEqual(second['commandId'], first['commandId'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
