"""P2: 탐지 배치 계약·사람-보호구 연결 규칙·컷 전환 추적 분리를 실제 PostgreSQL로 검증한다.

합성 배치 검증은 표준 Python으로 충분하지만 테스트 영상 검증은 YOLO가 필요하므로
`.venv/bin/python tests/detections.py`로 실행한다. 검증 장치와 배치는 종료 시 삭제한다.
"""
from datetime import datetime, timedelta, timezone
import importlib.util
from urllib import parse
import unittest
import uuid

from integration import Session
from local_fixtures import ROOT, DeviceFixture, sql

VIDEO = ROOT.parent / 'iot-server/assets/test-video.mp4'
START = datetime(2026, 9, 30, 1, 0, 0, tzinfo=timezone.utc)


def iso(t):
    return t.isoformat(timespec='microseconds')


def load_simulator():
    spec = importlib.util.spec_from_file_location('simulate_video_iot', ROOT / 'scripts/simulate-video-iot.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class DeviceCase(unittest.TestCase):
    def setUp(self):
        self.fixture = DeviceFixture()
        self.addCleanup(self.fixture.cleanup)  # detection_* 는 장치 삭제로 함께 지워진다(ON DELETE CASCADE).
        self.iot = {'Authorization': 'Bearer ' + self.fixture.token}
        self.assertEqual(Session().call('POST', '/iot/register', {'deviceId': self.fixture.id, 'name': '일시 검증 장치'}, self.iot)[0], 200)
        self.admin = Session(); self.assertEqual(self.admin.login()[0], 200)

    def send(self, batch, headers=None):
        return Session().call('POST', '/iot/detections', batch, headers or self.iot)

    def tracks(self, session, start, end, camera='cam-1'):
        query = parse.urlencode({'deviceId': self.fixture.id, 'cameraId': camera, 'from': iso(start), 'to': iso(end)})
        return session.call('GET', '/detections/tracks?' + query)

    def count(self, table):
        return int(sql(f"SELECT count(*) FROM {table} WHERE device_id='{self.fixture.id}'"))


def person(track, box, score=0.9):
    return {'cls': 'person', 'score': score, 'box': box, 'trackId': track}


def gear(cls, box, score=0.8):
    return {'cls': cls, 'score': score, 'box': box}


class Contract(DeviceCase):
    def batch(self, frames, cuts=(), start=START, seconds=10):
        return {'batchId': str(uuid.uuid4()), 'deviceId': self.fixture.id, 'cameraId': 'cam-1', 'sessionId': str(uuid.uuid4()),
                'startedAt': iso(start), 'endedAt': iso(start + timedelta(seconds=seconds)), 'model': 'verify-model',
                'tracker': 'bytetrack', 'cuts': [iso(START + timedelta(seconds=c)) for c in cuts],
                'frames': [{'at': iso(START + timedelta(seconds=t)), 'objects': objects} for t, objects in frames]}

    def test_association_rules_and_idempotency(self):
        a, b, c = [0.1, 0.1, 0.3, 0.9], [0.6, 0.1, 0.8, 0.9], [0.4, 0.2, 0.55, 0.9]
        head_hat = [0.15, 0.08, 0.25, 0.2]      # a의 머리 영역
        waist_hat = [0.62, 0.45, 0.72, 0.55]    # b가 허리 높이에 든 안전모
        vest_b = [0.62, 0.3, 0.78, 0.6]
        frames = []
        for i in range(3):
            objects = [person('a', a), person('b', b), person('c', c), person(None, [0.9, 0.5, 0.99, 0.99], 0.5),
                       gear('hard_hat', head_hat), gear('hard_hat', waist_hat), gear('safety_vest', vest_b)]
            if i == 0:
                objects.append(gear('hard_hat', [0.44, 0.17, 0.51, 0.26]))  # c는 한 프레임만 → 근거 부족
            frames.append((1 + i, objects))
        body = self.batch(frames)
        code, result = self.send(body)
        self.assertEqual((code, result['duplicate']), (200, False), result)
        row = sql(f"SELECT untracked_persons,unassigned_hard_hats,unassigned_vests,frame_count,rule_version FROM detection_batches WHERE batch_id='{body['batchId']}'")
        self.assertEqual(row.strip(), '3|3|0|3|ppe-assoc-v1')

        code, tracks = self.tracks(self.admin, START, START + timedelta(seconds=10))
        self.assertEqual(code, 200, tracks)
        states = {t['trackId']: (t['helmet'], t['vest'], t['helmetFrames'], t['vestFrames'], t['frames']) for t in tracks}
        self.assertEqual(states, {'a': ('present', 'unknown', 3, 0, 3),
                                  'b': ('unknown', 'present', 0, 3, 3),   # 손에 든 안전모는 착용으로 연결하지 않음
                                  'c': ('unknown', 'unknown', 1, 0, 3)})  # 미탐지를 absent로 바꾸지 않음
        self.assertEqual(tracks[0]['firstSeen'][:19], '2026-09-30T01:00:01')

        # 재전송은 추가 저장 없음, 같은 ID의 다른 내용은 거부
        self.assertEqual(self.send(body)[1]['duplicate'], True)
        self.assertEqual((self.count('detection_batches'), self.count('detection_tracks')), (1, 3))
        changed = dict(body, frames=body['frames'][:2])
        self.assertEqual(self.send(changed), (409, {'error': 'BATCH_ID_CONFLICT'}))

    def test_rejections(self):
        ok = [(1, [person('a', [0.1, 0.1, 0.3, 0.9])])]
        self.assertEqual(self.send(self.batch(ok), {'Authorization': 'Bearer wrong'})[0], 401)
        self.assertEqual(self.send(dict(self.batch(ok), deviceId='verify-other-device'))[0], 403)
        cases = {
            'INVALID_WINDOW': self.batch(ok, seconds=61),
            'FRAME_OUTSIDE_WINDOW': self.batch([(10, ok[0][1])]),
            'FRAMES_NOT_SORTED': self.batch([(2, []), (1, [])]),
            'INVALID_BOX': self.batch([(1, [person('a', [0.5, 0.1, 0.3, 0.9])])]),
            'INVALID_TRACK': self.batch([(1, [dict(gear('hard_hat', [0.1, 0.1, 0.2, 0.2]), trackId='h1')])]),
            'CUT_OUTSIDE_WINDOW': self.batch(ok, cuts=[11]),
            # 컷(3초) 앞뒤에 같은 추적 ID
            'TRACK_SPANS_CUT': self.batch([(1, ok[0][1]), (4, ok[0][1])], cuts=[3]),
        }
        for code, batch in cases.items():
            with self.subTest(code):
                self.assertEqual(self.send(batch), (400, {'error': code}))
        self.assertEqual(self.send(self.batch([(1, [dict(ok[0][1][0], box=[0.1, 0.1, 1.2, 0.9])])]))[0], 400)
        # 컷 앞은 s0, 뒤는 s1이면 통과
        split = self.batch([(1, [person('s0-t1', [0.1, 0.1, 0.3, 0.9])]), (4, [person('s1-t1', [0.1, 0.1, 0.3, 0.9])])], cuts=[3])
        self.assertEqual(self.send(split)[0], 200)
        self.assertEqual(self.count('detection_batches'), 1)

        # 관리자 조회: 비로그인 401, 기간 오류 400
        self.assertEqual(self.tracks(Session(), START, START + timedelta(seconds=10))[0], 401)
        self.assertEqual(self.tracks(self.admin, START, START)[0], 400)
        self.assertEqual(self.tracks(self.admin, START, START + timedelta(days=2))[0], 400)


@unittest.skipUnless(VIDEO.exists() and importlib.util.find_spec('ultralytics'), '테스트 영상·ultralytics 필요(pipeline venv)')
class TestVideo(DeviceCase):
    """모의 IoT가 테스트 영상을 처리해 보낸 배치: 컷 전환 추적 분리, 재전송 중복 없음, absent 미생성."""

    @classmethod
    def setUpClass(cls):
        cls.sim = load_simulator()
        cls.reset = cls.sim.analyze(reset_on_cut=True)
        cls.naive = cls.sim.analyze(reset_on_cut=False)

    def test_video_batches(self):
        sim, camera = self.sim, 'test-video'
        self.assertGreater(len(self.reset['cuts']), 5)
        # 추적기를 초기화하지 않으면 ID가 컷을 넘어 이어진다 → 중앙이 거부해야 한다.
        naive = sim.batches(self.naive, self.fixture.id, camera, START, uuid.uuid4())
        rejected = [b['startedAt'] for b in naive if sim.send(b, self.fixture.token)[1] == {'error': 'TRACK_SPANS_CUT'}]
        print('초기화 없는 추적에서 거부된 배치 구간 시작:', [r[11:19] for r in rejected])
        self.assertTrue(rejected)

        batches = sim.batches(self.reset, self.fixture.id, camera, START, uuid.uuid4())
        sql(f"DELETE FROM detection_batches WHERE device_id='{self.fixture.id}'")
        self.assertEqual(len(batches), 9)
        for b in batches:
            code, body = sim.send(b, self.fixture.token)
            self.assertEqual((code, body['duplicate']), (200, False), body)
        stored = (self.count('detection_batches'), self.count('detection_tracks'))
        for b in batches:
            self.assertTrue(sim.send(b, self.fixture.token)[1]['duplicate'])
        self.assertEqual((self.count('detection_batches'), self.count('detection_tracks')), stored)

        code, tracks = self.tracks(self.admin, START, START + timedelta(seconds=90), camera)
        self.assertEqual(code, 200)
        self.assertEqual(len(tracks), stored[1])
        cuts = [START + timedelta(seconds=c) for c in self.reset['cuts']]
        for t in tracks:
            first, last = datetime.fromisoformat(t['firstSeen']), datetime.fromisoformat(t['lastSeen'])
            self.assertFalse(any(first < c <= last for c in cuts), t)
            self.assertIn(t['helmet'], ('present', 'unknown'))
            self.assertIn(t['vest'], ('present', 'unknown'))

        def within(a, b):
            return [t for t in tracks if START + timedelta(seconds=a) <= datetime.fromisoformat(t['firstSeen']) < START + timedelta(seconds=b)]
        # 정답표 초안: 10–20초 두 사람 모두 안전모 미착용(한 명은 손에 듦) → present가 나오면 안 된다.
        self.assertFalse([t for t in within(10, 20) if t['helmet'] == 'present'])
        # 40–50초 오른쪽 사람 착용: YOLO가 탐지한 안전모가 머리 영역으로 연결된다.
        self.assertTrue([t for t in within(40, 50) if t['helmet'] == 'present'])
        for t in tracks:
            offset = (datetime.fromisoformat(t['firstSeen']) - START).total_seconds()
            end = (datetime.fromisoformat(t['lastSeen']) - START).total_seconds()
            print(f"{t['trackId']:>9} {offset:6.2f}–{end:6.2f}초 프레임 {t['frames']:3d} 안전모 {t['helmet']}({t['helmetFrames']}) 조끼 {t['vest']}({t['vestFrames']})")


if __name__ == '__main__':
    unittest.main(verbosity=2)
