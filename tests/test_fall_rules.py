"""쓰러짐 판정 규칙(scripts/fall_rules.py)을 합성 키포인트로 검사한다. 모델 없이 실행된다."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from fall_rules import FallConfig, FallDetector, posture

FPS = 10


def upright(x=300, tid=1):
    # 세로로 긴 사람: 박스 0.35, 키포인트 가로/세로 약 0.3
    kp = [[x + (i % 3 - 1) * 15, 100 + i * 15, 0.9] for i in range(17)]
    return {'id': tid, 'box': [x - 40, 90, x + 40, 330], 'kp': kp}


def lying(x=300, tid=1):
    # 서 있던 사람의 발(y=330) 근처 바닥에 옆으로 누운 모습
    kp = [[x - 120 + i * 15, 315 + (i % 3 - 1) * 10, 0.9] for i in range(17)]
    return {'id': tid, 'box': [x - 130, 295, x + 130, 345], 'kp': kp}


def crouch(x=300, tid=1):
    kp = [[x - 40 + (i % 5) * 20, 220 + i * 7, 0.9] for i in range(17)]
    return {'id': tid, 'box': [x - 60, 210, x + 60, 340], 'kp': kp}


def run(sequence, cfg=None):
    """sequence: [(초, 사람 목록 함수 또는 None)] 구간들. 이벤트 목록을 돌려준다."""
    detector, events, t = FallDetector(cfg or FallConfig(hold_seconds=1.0)), [], 0.0
    for seconds, make in sequence:
        for _ in range(round(seconds * FPS)):
            events += detector.update(t, [make()] if make else [])
            t += 1 / FPS
    return events


class PostureTest(unittest.TestCase):
    def test_postures(self):
        cfg = FallConfig()
        self.assertEqual(posture(upright(), cfg), 'upright')
        self.assertEqual(posture(lying(), cfg), 'lying')
        self.assertNotEqual(posture(crouch(), cfg), 'lying')
        # 키포인트가 부족하면 박스 비율만 본다.
        few = dict(lying(), kp=[[0, 0, 0.1]] * 17)
        self.assertEqual(posture(few, cfg), 'lying')


class DetectorTest(unittest.TestCase):
    def test_fast_fall_confirms_after_hold(self):
        events = run([(2, upright), (2, lying)])
        self.assertEqual([e['type'] for e in events], ['FALL_START'])
        self.assertAlmostEqual(events[0]['at'] - events[0]['fallStartedAt'], 1.0, delta=0.11)

    def test_short_lying_is_not_confirmed(self):
        self.assertEqual(run([(2, upright), (0.5, lying), (2, upright)]), [])

    def test_lying_without_recent_upright_is_ignored(self):
        # 처음부터 누워 있던 사람, 서 있다가 사라진 뒤 한참 후 누운 사람(천천히 눕기)은 제외한다.
        self.assertEqual(run([(3, lying)]), [])
        self.assertEqual(run([(2, upright), (2, None), (3, lying)]), [])

    def test_crouch_and_bend_are_not_falls(self):
        self.assertEqual(run([(2, upright), (3, crouch), (2, upright)]), [])

    def test_missed_detections_and_id_switch_keep_candidate(self):
        # 누운 사람을 포즈 모델이 잠깐 놓치고, 다시 잡을 때 추적 ID가 바뀌어도 같은 후보다.
        events = run([(2, upright), (0.4, lying), (0.4, None), (1, lambda: lying(tid=7))])
        self.assertEqual([e['type'] for e in events], ['FALL_START'])
        self.assertEqual(events[0]['trackId'], 7)

    def test_id_switch_at_fall_moment(self):
        events = run([(2, upright), (2, lambda: lying(tid=9))])
        self.assertEqual([e['type'] for e in events], ['FALL_START'])
        # 멀리 떨어진 곳에서 새로 보인 누운 사람은 서 있던 사람과 연결하지 않는다.
        self.assertEqual(run([(2, upright), (2, lambda: lying(x=1200, tid=9))]), [])

    def test_end_on_recovery_or_loss(self):
        events = run([(2, upright), (2, lying), (3, upright)])
        self.assertEqual([(e['type'], e.get('reason')) for e in events], [('FALL_START', None), ('FALL_END', 'recovered')])
        events = run([(2, upright), (2, lying), (6, None)])
        self.assertEqual([(e['type'], e.get('reason')) for e in events], [('FALL_START', None), ('FALL_END', 'lost')])

    def test_other_person_does_not_cancel(self):
        # 쓰러진 사람 옆에 다른 사람(다른 ID)이 서 있어도 후보를 취소하지 않는다.
        cfg = FallConfig(hold_seconds=1.0)
        detector, events, t = FallDetector(cfg), [], 0.0
        for i in range(40):
            persons = [upright()] if i < 20 else [lying(), upright(x=320, tid=2)]
            events += detector.update(t, persons)
            t += 0.1
        self.assertEqual([e['type'] for e in events], ['FALL_START'])


if __name__ == '__main__':
    unittest.main()
