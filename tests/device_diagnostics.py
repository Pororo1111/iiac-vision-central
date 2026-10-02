"""장치 진단(명령 반영 확인·오류 보고·재시작·로그 요청)이 heartbeat로 오가는지 실제 PostgreSQL로 검증한다."""
import unittest
import uuid

from integration import Session
from local_fixtures import DeviceFixture


class DeviceDiagnostics(unittest.TestCase):
    def setUp(self):
        self.fixture = DeviceFixture()
        self.addCleanup(self.fixture.cleanup)
        self.iot = {'Authorization': 'Bearer ' + self.fixture.token}
        self.admin = Session(); self.assertEqual(self.admin.login()[0], 200)
        self.csrf = self.admin.csrf()
        self.path = f'/devices/{self.fixture.id}'
        self.assertEqual(Session().call('POST', '/iot/register', {'deviceId': self.fixture.id, 'name': '일시 검증 장치'}, self.iot)[0], 200)

    def beat(self, status='RUNNING', **extra):
        body = {'deviceId': self.fixture.id, 'modelName': 'best.pt', 'runtimeStatus': status, **extra}
        return Session().call('POST', '/iot/heartbeat', body, self.iot)

    def test_monitoring_ack_and_error(self):
        self.assertEqual(self.beat()[1], {'accepted': True, 'monitoring': None, 'deployment': None, 'restart': None, 'logRequest': None})
        self.admin.call('POST', self.path + '/monitoring', {'action': 'RESUME'}, self.csrf)
        command = self.beat('STOPPED')[1]['monitoring']['commandId']
        device = self.admin.call('GET', self.path)[1]
        self.assertFalse(device['monitoringApplied'])
        self.assertEqual(self.beat('ERROR', monitoringCommandId=command, lastError='영상 소스를 열 수 없습니다.')[0], 200)
        device = self.admin.call('GET', self.path)[1]
        self.assertEqual((device['monitoringApplied'], device['runtimeStatus'], device['lastError']), (True, 'ERROR', '영상 소스를 열 수 없습니다.'))
        # 확인 필드를 보내지 않는 heartbeat는 반영 기록을 지우지 않고, 오류는 없어진 것으로 본다.
        self.beat('RUNNING')
        device = self.admin.call('GET', self.path)[1]
        self.assertEqual((device['monitoringApplied'], device['lastError']), (True, None))
        self.assertEqual(self.beat(lastError='x' * 501)[0], 400)
        self.assertEqual(self.beat(monitoringCommandId='not-a-uuid')[0], 400)

    def test_restart_command(self):
        self.assertEqual(self.admin.call('POST', self.path + '/restart', {'target': 'ALL'})[0], 403)
        self.assertEqual(self.admin.call('POST', self.path + '/restart', {'target': 'REBOOT'}, self.csrf)[0], 400)
        self.assertEqual(self.admin.call('POST', '/devices/missing-device/restart', {'target': 'ALL'}, self.csrf)[0], 404)
        self.assertEqual(Session().call('POST', self.path + '/restart', {'target': 'ALL'}, self.iot)[0], 403)
        status, device = self.admin.call('POST', self.path + '/restart', {'target': 'WORKER'}, self.csrf)
        self.assertEqual((status, device['restartTarget'], device['restartApplied']), (200, 'WORKER', False))
        restart = self.beat()[1]['restart']
        self.assertEqual(restart['target'], 'WORKER')
        self.assertEqual(self.beat()[1]['restart']['commandId'], restart['commandId'])
        # IoT가 처리했다고 보고하면 더 이상 전달하지 않는다.
        self.assertIsNone(self.beat(restartCommandId=restart['commandId'], startedAt='2026-09-28T07:00:00Z')[1]['restart'])
        device = self.admin.call('GET', self.path)[1]
        self.assertTrue(device['restartApplied'])
        self.assertTrue(device['startedAt'].startswith('2026-09-28T07:00'))

    def test_log_request_and_upload(self):
        self.assertEqual(self.admin.call('POST', self.path + '/logs/request', {'lines': 5}, self.csrf)[0], 400)
        status, state = self.admin.call('POST', self.path + '/logs/request', {'lines': 300}, self.csrf)
        self.assertEqual((status, state['pending'], state['logs']), (200, True, []))
        request = self.beat()[1]['logRequest']
        self.assertEqual((request['requestId'], request['lines']), (state['request']['requestId'], 300))
        upload = {'deviceId': self.fixture.id, 'requestId': request['requestId'], 'collectedAt': '2026-09-28T07:00:00Z',
                  'logs': [{'service': 'iot-api', 'text': 'started\nERROR camera'}, {'service': 'iot-worker', 'text': 'ok'}]}
        self.assertEqual(Session().call('POST', '/iot/logs', {**upload, 'requestId': str(uuid.uuid4())}, self.iot), (409, {'error': 'LOG_REQUEST_MISMATCH'}))
        self.assertEqual(Session().call('POST', '/iot/logs', {**upload, 'deviceId': 'other-device'}, self.iot)[0], 403)
        self.assertEqual(Session().call('POST', '/iot/logs', upload, self.iot), (200, {'accepted': True, 'duplicate': False}))
        self.assertEqual(Session().call('POST', '/iot/logs', upload, self.iot), (200, {'accepted': True, 'duplicate': True}))
        self.assertIsNone(self.beat()[1]['logRequest'])
        logs = self.admin.call('GET', self.path + '/logs')[1]
        self.assertFalse(logs['pending'])
        self.assertEqual([(l['service'], l['content']) for l in logs['logs']], [('iot-api', 'started\nERROR camera'), ('iot-worker', 'ok')])
        # 새 요청이 오면 이전 로그는 보여 주되 대기 상태가 된다.
        self.assertTrue(self.admin.call('POST', self.path + '/logs/request', {'lines': 100}, self.csrf)[1]['pending'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
