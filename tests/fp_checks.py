"""오탐 확인(V33)·관리자 검토(V34) 계약을 실제 PostgreSQL·실행 중인 중앙으로 검증한다. Gemma는 부르지 않고 워커 API를 직접 호출한다.

- 원본 프레임 저장 시 그 프레임에 hard_hat·safety_vest가 있을 때만 확인 대상이 생긴다(테스트 영상 카메라 제외).
- 워커 완료는 보호구 박스마다 판정이 하나씩 있어야 하고, 오탐·애매가 있으면 PENDING, 모두 정탐이면 NOT_NEEDED다.
- 관리자 승인은 원본과 오탐 박스만 뺀 라벨을 데이터셋 폴더에 쓰고, train·val이 모두 생기면 local:admin-fp로 집계한다.
- 반려·중복 처리·잘못된 박스 지정을 막고, 조회·검토는 관리자 세션(+CSRF), 워커 API는 워커 Bearer가 필요하다.

실행: .venv/bin/python tests/fp_checks.py (CENTRAL_TEST_URL·CENTRAL_INTERNAL_URL로 대상 변경, ADMIN_FP_DATASET로 데이터셋 폴더 지정).
실제 대기열을 건드리지 않도록 claim은 검증 장치로 범위를 좁힌다. 검증 장치·파일·검증이 만든 데이터셋 행은 종료 시 지운다.
"""
from datetime import timedelta
import json
import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from urllib import request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detection_snapshots import START, iso, jpeg, person, raw  # noqa: E402
from integration import BASE, ENV, Session  # noqa: E402
from local_fixtures import DATA, ROOT, DeviceFixture, sql as run_sql  # noqa: E402

INTERNAL = os.environ.get('CENTRAL_INTERNAL_URL', 'http://127.0.0.1:8201') + '/internal/ai'
DATASET = Path(os.environ.get('ADMIN_FP_DATASET', ROOT / '.artifacts/ai-assets/datasets/admin-fp'))
WORKER = {'Authorization': 'Bearer ' + ENV['AI_WORKER_TOKEN'], 'Content-Type': 'application/json'}
VEST = {'cls': 'safety_vest', 'score': 0.81, 'box': [0.15, 0.3, 0.35, 0.6]}
HAT = {'cls': 'hard_hat', 'score': 0.66, 'box': [0.2, 0.1, 0.3, 0.2]}


def sql(statement):
    return run_sql(statement).strip()


def worker(path, body=None):
    return raw('POST', INTERNAL + path, json.dumps(body or {}).encode(), WORKER)


class FpChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.external_before = sql("SELECT count(*) FROM external_datasets WHERE url='local:admin-fp'")
        cls.admin = Session()
        assert cls.admin.login()[0] == 200

    def setUp(self):
        self.fixture = DeviceFixture()
        self.batches = []
        self.addCleanup(self.cleanup)
        self.assertEqual(Session().call('POST', '/iot/register', {'deviceId': self.fixture.id, 'name': '일시 검증 장치'},
                                        {'Authorization': 'Bearer ' + self.fixture.token})[0], 200)

    def cleanup(self):
        names = sql(f"SELECT dataset_split || '/' || dataset_name FROM fp_checks WHERE device_id='{self.fixture.id}' AND dataset_name IS NOT NULL")
        for item in filter(None, names.splitlines()):
            split, name = item.split('/')
            (DATASET / 'images' / split / name).unlink(missing_ok=True)
            (DATASET / 'labels' / split / name.replace('.jpg', '.txt')).unlink(missing_ok=True)
        self.fixture.cleanup()
        for batch in self.batches:
            shutil.rmtree(DATA / 'media/detection-snapshots' / batch, ignore_errors=True)
        # 검증이 외부 데이터셋 행을 새로 만들었으면 지우고, 원래 있었으면 실제 승인 기록으로 수를 다시 맞춘다.
        if self.external_before == '0':
            sql("DELETE FROM external_datasets WHERE url='local:admin-fp'")
        else:
            sql("""UPDATE external_datasets SET counts=jsonb_build_object(
                     'train',(SELECT count(*) FROM fp_checks WHERE review_status='APPROVED' AND dataset_split='train'),
                     'val',(SELECT count(*) FROM fp_checks WHERE review_status='APPROVED' AND dataset_split='val'),'test',0,
                     'boxes',(SELECT coalesce(sum(dataset_boxes),0) FROM fp_checks WHERE review_status='APPROVED'))
                   WHERE url='local:admin-fp'""")

    def send(self, objects, offset=0, camera='usb-0'):
        """프레임 1개 배치와 원본 1장을 보낸다(IoT 사람 감지 전송 형식)."""
        at = START + timedelta(seconds=offset)
        body = {'batchId': str(uuid.uuid4()), 'deviceId': self.fixture.id, 'cameraId': camera, 'sessionId': str(uuid.uuid4()),
                'startedAt': iso(at), 'endedAt': iso(at + timedelta(milliseconds=1)), 'model': 'verify-model',
                'tracker': 'bytetrack', 'cuts': [], 'frames': [{'at': iso(at), 'objects': objects}]}
        code, result = Session().call('POST', '/iot/detections', body, {'Authorization': 'Bearer ' + self.fixture.token})
        self.assertEqual(code, 200, result)
        self.batches.append(body['batchId'])
        boundary = 'snap-' + uuid.uuid4().hex
        fields = {'batchId': body['batchId'], 'deviceId': self.fixture.id, 'at': iso(at)}
        data = b''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
        data += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="f.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'
                 ).encode() + jpeg('gray') + f'\r\n--{boundary}--\r\n'.encode()
        code, result = raw('POST', BASE + '/api/iot/detection-snapshots', data,
                           {'Content-Type': 'multipart/form-data; boundary=' + boundary, 'Authorization': 'Bearer ' + self.fixture.token})
        self.assertEqual(code, 200, result)
        return body['batchId']

    def claim(self):
        code, job = worker('/fp-checks/claim?deviceId=' + self.fixture.id)
        self.assertEqual(code, 200, job)
        return job

    def judged(self, objects, verdicts, offset=0):
        """보내고 워커 판정까지 마친 fp_checks id."""
        self.send(objects, offset)
        job = self.claim()
        code, result = worker(f"/fp-checks/{job['id']}/complete", {'leaseToken': job['leaseToken'], 'model': 'gemma4:12b',
                              'promptVersion': 'fp-v4', 'elapsedMs': 10, 'verdicts': verdicts})
        self.assertEqual(code, 200, result)
        return job['id']

    def review(self, check, body):
        return self.admin.call('POST', f'/training/fp-checks/{check}/review', body, self.admin.csrf())

    def test_enqueue_only_ppe_frames(self):
        self.send([person()], 0)
        self.send([person(), VEST], 1, camera='test-video-0')  # 테스트 영상 카메라는 제외
        batch = self.send([person(), VEST], 2)
        self.assertEqual(sql(f"SELECT count(*) FROM fp_checks WHERE device_id='{self.fixture.id}'"), '1')
        job = self.claim()
        self.assertEqual(job['batchId'], batch)
        self.assertEqual([o['cls'] for o in job['objects']], ['person', 'safety_vest'])
        self.assertEqual(job['objects'][1]['box'], VEST['box'])
        self.assertTrue(Path(job['path']).is_file())
        self.assertEqual((job['width'], job['height']), (64, 64))
        self.assertEqual(worker('/fp-checks/claim?deviceId=' + self.fixture.id)[0], 204)

    def test_completion_sets_review_status(self):
        self.send([person(), HAT, VEST], 0)
        job = self.claim()
        base = {'leaseToken': job['leaseToken'], 'model': 'gemma4:12b', 'promptVersion': 'fp-v4', 'elapsedMs': 10}
        hat = {'index': 1, 'verdict': 'false', 'seen': '대머리', 'reason': '머리카락이 없는 머리'}
        vest = {'index': 2, 'verdict': 'real', 'seen': '형광 조끼', 'reason': '반사띠'}
        self.assertEqual(worker(f"/fp-checks/{job['id']}/complete", {**base, 'verdicts': [hat]})[1]['error'], 'INVALID_VERDICTS')
        self.assertEqual(worker(f"/fp-checks/{job['id']}/complete", {**base, 'verdicts': [hat, vest, {**vest, 'index': 0}]})[1]['error'],
                         'INVALID_VERDICTS')
        self.assertEqual(worker(f"/fp-checks/{job['id']}/complete", {**base, 'verdicts': [hat, vest]})[0], 200)
        self.assertEqual(sql(f"SELECT status, review_status, false_count, unsure_count FROM fp_checks WHERE id='{job['id']}'"), 'DONE|PENDING|1|0')
        verdicts = json.loads(sql(f"SELECT verdicts FROM fp_checks WHERE id='{job['id']}'"))
        self.assertEqual([(v['index'], v['cls'], v['verdict']) for v in verdicts], [(1, 'hard_hat', 'false'), (2, 'safety_vest', 'real')])
        self.assertEqual(verdicts[0]['box'], HAT['box'])
        self.assertEqual(worker(f"/fp-checks/{job['id']}/complete", {**base, 'verdicts': [hat, vest]})[0], 409)
        # 모두 정탐이면 확인 불필요
        check = self.judged([person(), VEST], [{'index': 1, 'verdict': 'real', 'seen': '', 'reason': ''}], 1)
        self.assertEqual(sql(f"SELECT review_status FROM fp_checks WHERE id='{check}'"), 'NOT_NEEDED')
        self.assertEqual(self.review(check, {'action': 'APPROVE', 'removed': []})[1]['error'], 'REVIEW_NOT_PENDING')

    def test_approve_writes_dataset_and_registers(self):
        first = self.judged([person(), VEST], [{'index': 1, 'verdict': 'false', 'seen': '베이지 셔츠', 'reason': '반사띠 없음'}], 0)
        second = self.judged([person(), HAT, VEST], [{'index': 1, 'verdict': 'unsure', 'seen': '', 'reason': ''},
                                                     {'index': 2, 'verdict': 'false', 'seen': '', 'reason': ''}], 1)
        # 보호구가 아닌 박스(사람)나 없는 박스는 오탐으로 지정할 수 없다
        self.assertEqual(self.review(first, {'action': 'APPROVE', 'removed': [0]})[1]['error'], 'INVALID_REMOVED')
        self.assertEqual(self.review(first, {'action': 'APPROVE', 'removed': [5]})[1]['error'], 'INVALID_REMOVED')
        code, result = self.review(first, {'action': 'APPROVE', 'removed': [1]})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['item']['reviewStatus'], 'APPROVED')
        split, name = sql(f"SELECT dataset_split, dataset_name FROM fp_checks WHERE id='{first}'").split('|')
        label = (DATASET / 'labels' / split / name.replace('.jpg', '.txt')).read_text().split()
        self.assertEqual(label[0], '0')  # 사람만 남고 조끼(오탐)는 빠진다
        self.assertEqual(len(label), 5)
        self.assertTrue((DATASET / 'images' / split / name).is_file())
        self.assertTrue((DATASET / 'data.yaml').is_file())
        self.assertEqual(self.review(first, {'action': 'REJECT'})[1]['error'], 'REVIEW_NOT_PENDING')
        # 애매(#1 안전모)를 관리자가 정탐으로, #2 조끼를 오탐으로 정해 승인
        code, result = self.review(second, {'action': 'APPROVE', 'removed': [2]})
        self.assertEqual(code, 200, result)
        split2, name2 = sql(f"SELECT dataset_split, dataset_name FROM fp_checks WHERE id='{second}'").split('|')
        lines = (DATASET / 'labels' / split2 / name2.replace('.jpg', '.txt')).read_text().splitlines()
        self.assertEqual(sorted(line.split()[0] for line in lines), ['0', '3'])
        self.assertEqual(sql(f"SELECT removed FROM fp_checks WHERE id='{second}'"), '[2]')
        datasets = result['dataset']
        self.assertGreaterEqual(datasets['train'] + datasets['val'], 2)
        if datasets['train'] and datasets['val']:
            self.assertEqual(sql("SELECT status FROM external_datasets WHERE url='local:admin-fp'"), 'READY')

    def test_reject_and_admin_views(self):
        check = self.judged([person(), HAT], [{'index': 1, 'verdict': 'false', 'seen': '대머리', 'reason': ''}], 0)
        code, view = self.admin.call('GET', '/training/fp-checks?review=PENDING&limit=200')
        self.assertEqual(code, 200)
        self.assertIn(check, [i['item']['id'] for i in view['items']])
        self.assertEqual(view['dataset']['url'], 'local:admin-fp')
        code, detail = self.admin.call('GET', f'/training/fp-checks/{check}')
        self.assertEqual(code, 200)
        self.assertEqual([o['cls'] for o in detail['objects']], ['person', 'hard_hat'])
        with self.admin.opener.open(request.Request(BASE + detail['imagePath']), timeout=10) as response:
            self.assertEqual(response.headers['Content-Type'], 'image/jpeg')
        self.assertEqual(self.admin.call('POST', f'/training/fp-checks/{check}/review', {'action': 'REJECT'})[0], 403)  # CSRF 없음
        code, result = self.review(check, {'action': 'REJECT'})
        self.assertEqual((code, result['item']['reviewStatus']), (200, 'REJECTED'))
        self.assertEqual(sql(f"SELECT dataset_name IS NULL FROM fp_checks WHERE id='{check}'"), 't')
        self.assertEqual(self.admin.call('GET', '/training/fp-checks?review=WRONG')[0], 400)
        self.assertEqual(Session().call('GET', '/training/fp-checks')[0], 401)

    def test_worker_auth_and_lease(self):
        self.assertEqual(raw('POST', INTERNAL + '/fp-checks/claim', b'{}', {'Content-Type': 'application/json'})[0], 401)
        self.send([person(), HAT], 0)
        job = self.claim()
        self.assertEqual(worker(f"/fp-checks/{job['id']}/release", {'leaseToken': job['leaseToken'], 'error': '연결 실패'})[0], 200)
        self.assertEqual(sql(f"SELECT status, attempts FROM fp_checks WHERE id='{job['id']}'"), 'QUEUED|0')
        job = self.claim()
        self.assertEqual(worker(f"/fp-checks/{job['id']}/complete", {'leaseToken': str(uuid.uuid4()), 'model': 'm', 'promptVersion': 'p',
                                'verdicts': [{'index': 1, 'verdict': 'real', 'seen': '', 'reason': ''}]})[1]['error'], 'LEASE_LOST')
        self.assertEqual(worker(f"/fp-checks/{job['id']}/fail", {'leaseToken': job['leaseToken'], 'error': '실패'})[0], 200)
        self.assertEqual(sql(f"SELECT status, attempts FROM fp_checks WHERE id='{job['id']}'"), 'QUEUED|1')


if __name__ == '__main__':
    unittest.main(verbosity=2)
