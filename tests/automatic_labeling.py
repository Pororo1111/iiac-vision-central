"""실제 PostgreSQL에서 자동 작업 등록·중복·Gemma 결과·재시도를 검증한다."""
import concurrent.futures
from datetime import datetime, timezone
import json
import unittest
import uuid
import labeling
from labeling import upload, worker
from local_fixtures import sql


class AutomaticLabeling(unittest.TestCase):
    setUp = labeling.PromptWorkflow.setUp

    def test_automatic_labeling(self):
        captured = datetime.now(timezone.utc).isoformat()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: upload(self.fixture, captured=captured), range(2)))
        self.assertEqual([code for code, _ in responses], [200, 200])
        sample = responses[0][1]
        self.assertEqual(sample['id'], responses[1][1]['id'])
        path = '/training/samples/' + sample['id']
        jobs = self.admin.call('GET', '/training/label-jobs?sampleId=' + sample['id'])[1]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['kind'], 'AUTO_LABEL')
        self.assertEqual(jobs[0]['status'], 'QUEUED')
        self.assertEqual(self.admin.call('POST', path + '/retry-label', {}, self.csrf)[0], 409)
        # 실데이터의 대기열을 소비하지 않고 테스트 작업에만 lease를 부여한다.
        job_id = str(uuid.UUID(jobs[0]['id']))
        token = str(uuid.uuid4())
        sql(f"UPDATE labeling_jobs SET status='RUNNING',lease_token='{token}',lease_until=now()+interval '120 seconds' WHERE id='{job_id}'")
        body = {'leaseToken': token, 'modelDigest': 'fixture-only', 'result': {'answer': '박스 검증', 'labels': ['person']}}
        body['result']['boxes'] = [{'label': 'person', 'x': .5, 'y': .5, 'width': .4, 'height': .6}]
        self.assertEqual(worker('/jobs/' + job_id + '/complete', body)[0], 409)
        body['result']['boxes'] = None
        self.assertEqual(worker('/jobs/' + job_id + '/complete', body)[0], 200)
        sample = self.admin.call('GET', path)[1]
        self.assertEqual(sample['status'], 'LABELED')
        self.assertIsNone(json.loads(sample['proposal'])['result']['boxes'])
        self.assertEqual(self.admin.call('POST', path + '/retry-label', {}, self.csrf)[0], 409)
        self.assertEqual(upload(self.fixture, captured=captured)[1]['id'], sample['id'])
        self.assertEqual(self.admin.call('GET', path)[1]['status'], 'LABELED')
        _, second = upload(self.fixture)
        sql(f"UPDATE labeling_jobs SET status='FAILED',finished_at=now() WHERE sample_id='{second['id']}'")
        self.assertEqual(self.admin.call('POST', '/training/samples/' + second['id'] + '/retry-label', {}, self.csrf)[0], 202)


if __name__ == '__main__':
    unittest.main(verbosity=2)
