"""쓰러짐 이벤트(V38) 계약을 실제 PostgreSQL로 검증한다.

- POST /api/iot/events: FALL_START·FALL_END는 zoneId 생략 가능(빈 문자열 저장), 침입은 zoneId 필수, 재전송 중복.
- FALL_START와 같은 UUID 탐지 배치의 구간 판정이 이벤트의 fallCheck(PENDING·CONFIRMED·NOT_CONFIRMED·UNAVAILABLE)가 된다.
- GET /api/alerts/falls: 확인하지 않은 최근 쓰러짐만. POST /api/events/{id}/ack: CSRF·인증·대상 검사, 재확인은 첫 기록 유지.

실행: CENTRAL_TEST_URL=http://127.0.0.1:8211 python3 tests/fall_events.py (기본은 8200). 검증 장치는 종료 시 삭제한다.
원본 사진을 보내지 않으므로 observer가 구간을 가져가지 않으며, 판정 상태는 SQL로 바꿔 확인한다.
"""
from datetime import datetime, timedelta, timezone
import unittest
import uuid

from integration import Session
from local_fixtures import DeviceFixture, sql


def iso(t):
    return t.isoformat(timespec='microseconds')


class FallEvents(unittest.TestCase):
    def setUp(self):
        self.fixture = DeviceFixture()
        self.addCleanup(self.fixture.cleanup)
        self.headers = {'Authorization': 'Bearer ' + self.fixture.token}
        self.iot = Session()
        self.assertEqual(self.iot.call('POST', '/iot/register', {'deviceId': self.fixture.id, 'name': '쓰러짐 검증'}, self.headers)[0], 200)
        self.admin = Session()
        self.assertEqual(self.admin.login()[0], 200)
        self.now = datetime.now(timezone.utc) - timedelta(seconds=30)

    def send(self, type_, event_id=None, zone=None, at=None, count=1):
        body = {'eventId': event_id or str(uuid.uuid4()), 'deviceId': self.fixture.id, 'cameraId': 'cam-1',
                'type': type_, 'occurredAt': iso(at or self.now), 'objectCount': count}
        if zone is not None:
            body['zoneId'] = zone
        return body, self.iot.call('POST', '/iot/events', body, self.headers)

    def batch(self, batch_id, at):
        body = {'batchId': batch_id, 'deviceId': self.fixture.id, 'cameraId': 'cam-1', 'sessionId': str(uuid.uuid4()),
                'startedAt': iso(at), 'endedAt': iso(at + timedelta(milliseconds=1)), 'model': 'yolo26n-pose',
                'tracker': 'bytetrack', 'cuts': [],
                'frames': [{'at': iso(at), 'objects': [{'cls': 'person', 'score': 0.9, 'box': [0.2, 0.6, 0.8, 0.9], 'trackId': 't1'}]}]}
        code, result = self.iot.call('POST', '/iot/detections', body, self.headers)
        self.assertEqual(code, 200, result)

    def listed(self, event_id):
        code, page = self.admin.call('GET', f'/events?deviceId={self.fixture.id}&size=100')
        self.assertEqual(code, 200)
        return next(e for e in page['items'] if e['eventId'] == event_id)

    def test_event_contract(self):
        body, (code, result) = self.send('FALL_START')
        self.assertEqual((code, result['duplicate']), (200, False))
        self.assertEqual(self.send('FALL_START', body['eventId'])[1][1]['duplicate'], True)
        self.assertEqual(self.send('FALL_END', zone='  ')[1][0], 200)
        self.assertEqual(self.send('INTRUSION_START')[1], (400, {'error': 'INVALID_REQUEST'}))
        self.assertEqual(self.send('INTRUSION_START', zone=' ')[1], (400, {'error': 'INVALID_REQUEST'}))
        self.assertEqual(self.send('INTRUSION_START', zone='zone-1')[1][0], 200)
        self.assertEqual(self.send('FALL_MAYBE')[1], (400, {'error': 'INVALID_REQUEST'}))

        code, page = self.admin.call('GET', f'/events?deviceId={self.fixture.id}&type=FALL_START')
        self.assertEqual((code, page['total']), (200, 1))
        start = page['items'][0]
        self.assertEqual((start['zoneId'], start['fallCheck'], start['ackedAt']), ('', 'PENDING', None))
        intrusion = self.admin.call('GET', f'/events?deviceId={self.fixture.id}&type=INTRUSION_START')[1]['items'][0]
        self.assertIsNone(intrusion['fallCheck'])
        self.assertEqual(self.admin.call('GET', '/events?type=FALL_MAYBE')[0], 400)

    def test_gemma_check(self):
        body, (code, _) = self.send('FALL_START')
        self.assertEqual(code, 200)
        eid = body['eventId']
        self.assertEqual(self.listed(eid)['fallCheck'], 'PENDING')  # 배치 전
        self.batch(eid, self.now)
        self.assertEqual(self.listed(eid)['fallCheck'], 'PENDING')  # 원본 대기
        done = "status='COMPLETED',summary='바닥에 사람이 누워 있다',people=1"
        sql(f"UPDATE observations SET {done},fallen=1 WHERE batch_id='{eid}'")
        e = self.listed(eid)
        self.assertEqual((e['fallCheck'], e['fallen'], e['checkSummary'], e['hasThumbnail']),
                         ('CONFIRMED', 1, '바닥에 사람이 누워 있다', False))
        sql(f"UPDATE observations SET fallen=0 WHERE batch_id='{eid}'")
        self.assertEqual(self.listed(eid)['fallCheck'], 'NOT_CONFIRMED')
        sql(f"UPDATE observations SET fallen=NULL WHERE batch_id='{eid}'")
        self.assertEqual(self.listed(eid)['fallCheck'], 'NOT_CONFIRMED')
        sql(f"UPDATE observations SET status='SKIPPED',skip_reason='dark' WHERE batch_id='{eid}'")
        self.assertEqual(self.listed(eid)['fallCheck'], 'UNAVAILABLE')

    def test_alert_ack(self):
        body, _ = self.send('FALL_START')
        eid = body['eventId']
        old, _ = self.send('FALL_START', at=datetime.now(timezone.utc) - timedelta(hours=25))
        intrusion, _ = self.send('INTRUSION_START', zone='zone-1')

        def open_ids():
            code, items = self.admin.call('GET', '/alerts/falls')
            self.assertEqual(code, 200)
            return [e['eventId'] for e in items]
        ids = open_ids()
        self.assertIn(eid, ids)
        self.assertNotIn(old['eventId'], ids)  # 24시간 지난 것은 제외
        self.assertNotIn(intrusion['eventId'], ids)

        self.assertEqual(Session().call('GET', '/alerts/falls')[0], 401)
        self.assertEqual(self.admin.call('POST', f'/events/{eid}/ack')[0], 403)  # CSRF 없음
        csrf = self.admin.csrf()
        self.assertEqual(self.admin.call('POST', f'/events/{intrusion["eventId"]}/ack', None, csrf), (400, {'error': 'NOT_FALL_EVENT'}))
        self.assertEqual(self.admin.call('POST', f'/events/{uuid.uuid4()}/ack', None, csrf), (404, {'error': 'EVENT_NOT_FOUND'}))
        self.assertEqual(self.admin.call('POST', '/events/not-uuid/ack', None, csrf)[0], 400)
        code, acked = self.admin.call('POST', f'/events/{eid}/ack', None, csrf)
        self.assertEqual(code, 200)
        self.assertIsNotNone(acked['ackedAt'])
        self.assertTrue(acked['ackedBy'])
        again = self.admin.call('POST', f'/events/{eid}/ack', None, csrf)[1]
        self.assertEqual(again['ackedAt'], acked['ackedAt'])
        self.assertNotIn(eid, open_ids())
        self.assertEqual(self.listed(eid)['ackedAt'], acked['ackedAt'])
        # 장치 삭제(정리) 때 확인 기록도 함께 지워진다.
        self.assertEqual(sql(f"SELECT count(*) FROM event_acks WHERE event_id='{eid}'").strip(), '1')


if __name__ == '__main__':
    unittest.main()
