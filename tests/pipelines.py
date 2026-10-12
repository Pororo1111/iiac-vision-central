"""파이프라인 마법사 API와 수집 → 라벨링 → 데이터셋 대기 전이, Roboflow 요청·자동 매핑을 검증한다.

학습 실행 생성 이후(실제 YOLO 학습)는 무겁기 때문에 여기서 다루지 않는다. 외부 데이터셋을 '내려받는 중'
상태로 고정해 파이프라인이 학습 실행을 만들기 직전에서 멈추게 한다. 생성 데이터는 종료 시 정리한다.
Roboflow 요청 검증은 이미 받아 둔 safety-cones v4 캐시를 사용한다(버전 조회 API만 호출).
"""
import time
import unittest
import uuid

from labeling import upload, worker
from integration import Session
from local_fixtures import DeviceFixture, sql

CACHED = 'https://universe.roboflow.com/roboflow-universe-projects/safety-cones-vfrj2/dataset/4'
BASELINE = 'https://universe.roboflow.com/roboflow-universe-projects/construction-site-safety/dataset/27'


def wait(check, seconds=60):
    for _ in range(seconds):
        value = check()
        if value:
            return value
        time.sleep(1)
    return check()


class Pipelines(unittest.TestCase):
    def setUp(self):
        self.fixture = DeviceFixture()
        self.addCleanup(self.fixture.cleanup)
        self.admin = Session(); self.assertEqual(self.admin.login()[0], 200)
        self.csrf = self.admin.csrf()
        self.assertEqual(Session().call('POST', '/iot/register', {'deviceId': self.fixture.id, 'name': '일시 검증 장치'},
                                        {'Authorization': 'Bearer ' + self.fixture.token})[0], 200)
        self.pipelines, self.externals = [], []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for pipeline in self.pipelines:
            sql(f"DELETE FROM pipelines WHERE id='{uuid.UUID(pipeline)}'")
        for external in self.externals:
            sql(f"DELETE FROM external_datasets WHERE id='{uuid.UUID(external)}'")

    def create(self, body):
        status, view = self.admin.call('POST', '/training/pipelines', body, self.csrf)
        if status == 202:
            self.pipelines.append(view['pipeline']['id'])
        return status, view

    def view(self, pipeline_id):
        return next(v for v in self.admin.call('GET', '/training/pipelines')[1] if v['pipeline']['id'] == pipeline_id)

    def test_base_models_and_validation(self):
        keys = [m['key'] for m in self.admin.call('GET', '/training/base-models')[1]]
        self.assertEqual(keys[:3], ['default', 'yolo26n', 'yolo26m'])
        options = {'baseModel': 'default', 'epochs': 1, 'confidence': 0.6}
        base = {'name': '검증', 'sourceKind': 'IOT', 'source': {}, 'external': [], 'options': options}
        self.assertEqual(self.create(base), (400, {'error': 'IOT_SOURCE_REQUIRED'}))
        self.assertEqual(self.create({**base, 'sourceKind': 'UPLOAD'}), (400, {'error': 'UPLOAD_SOURCE_REQUIRED'}))
        self.assertEqual(self.create({**base, 'source': {'deviceIds': ['없는-장치'], 'targetCount': 1}})[0], 409)
        self.assertEqual(self.create({**base, 'options': {**options, 'baseModel': '/etc/passwd'}})[0], 400)
        valid = {**base, 'source': {'deviceIds': [self.fixture.id], 'targetCount': 1}}
        self.assertEqual(self.create({**valid, 'options': {**options, 'baseModel': 'release:' + str(uuid.uuid4())}}),
                         (409, {'error': 'BASE_MODEL_NOT_FOUND'}))
        self.assertEqual(self.create({**base, 'options': {**options, 'epochs': 0}})[0], 400)
        self.assertEqual(self.admin.call('POST', '/training/pipelines', base)[0], 403)

    def test_roboflow_request(self):
        for url in ['https://example.com/a/b', 'https://universe.roboflow.com/only', 'javascript:alert(1)',
                    'https://universe.roboflow.com/a/b/dataset/0', 'https://universe.roboflow.com/a%2F/b']:
            self.assertEqual(self.admin.call('POST', '/training/external-datasets', {'url': url}, self.csrf),
                             (400, {'error': 'INVALID_ROBOFLOW_URL'}), url)
        existing = {e['id'] for e in self.admin.call('GET', '/training/external-datasets')[1]}
        status, created = self.admin.call('POST', '/training/external-datasets', {'url': CACHED + '?x=1'}, self.csrf)
        self.assertEqual(status, 202)
        if created['id'] not in existing:
            self.externals.append(created['id'])
        self.assertEqual(created['url'], CACHED)
        self.assertIsNone(created['leaseToken'])
        # 같은 버전을 다시 요청하면 같은 항목을 돌려준다.
        self.assertEqual(self.admin.call('POST', '/training/external-datasets', {'url': CACHED}, self.csrf)[1]['id'], created['id'])
        ready = wait(lambda: next((e for e in self.admin.call('GET', '/training/external-datasets')[1]
                                   if e['id'] == created['id'] and e['status'] in ('READY', 'FAILED')), None), 120)
        self.assertEqual((ready['status'], ready['error']), ('READY', None), 'AI 워커와 ROBOFLOW_API_KEY를 확인하세요.')
        import json
        classes, mapping = json.loads(ready['classes']), json.loads(ready['mapping'])
        self.assertEqual(dict(zip(classes, mapping)), {'Safety Cone': 2})
        # 기준 데이터셋에 이미 들어 있는 v27은 거절하고 목록에도 보이지 않는다.
        self.assertEqual(self.admin.call('POST', '/training/external-datasets', {'url': BASELINE}, self.csrf), (409, {'error': 'ALREADY_IN_BASELINE'}))
        self.assertFalse(any(e['project'] == 'construction-site-safety' and e['version'] == 27 for e in self.admin.call('GET', '/training/external-datasets')[1]))
        self.assertNotIn('yamlPath', ready)

    def test_iot_pipeline_stages(self):
        # 워커가 가져가지 않도록 임대 중인 가짜 외부 데이터셋으로 데이터셋 단계에서 멈추게 한다.
        external = str(uuid.uuid4()); self.externals.append(external)
        sql(f"""INSERT INTO external_datasets(id,url,workspace,project,version,status,lease_token,lease_until)
                VALUES('{external}','https://universe.roboflow.com/verify/fixture','verify','fixture',1,'RUNNING',gen_random_uuid(),now()+interval '1 hour')""")
        status, view = self.create({'name': '검증 IoT 파이프라인', 'sourceKind': 'IOT',
            'source': {'deviceIds': [self.fixture.id], 'targetCount': 2, 'includeExisting': False},
            'external': [{'id': external}], 'options': {'baseModel': 'yolo26n', 'epochs': 1, 'confidence': 0.6}})
        self.assertEqual(status, 202)
        pipeline = view['pipeline']['id']
        self.assertEqual(view['pipeline']['stage'], 'COLLECTING')
        self.assertIn('0/2', view['pipeline']['message'])
        samples = [upload(self.fixture)[1] for _ in range(2)]
        self.assertTrue(wait(lambda: self.view(pipeline)['pipeline']['stage'] == 'LABELING', 30))
        self.assertEqual(self.view(pipeline)['pipeline']['sampleCount'], 2)
        # 실행 중인 워커가 아직 가져가지 않은 검증 작업만 임대해 완료 처리한다. 이미 가져갔으면 실제 결과를 기다린다.
        for sample in samples:
            job = str(uuid.UUID(self.admin.call('GET', '/training/label-jobs?sampleId=' + sample['id'])[1][0]['id']))
            token = str(uuid.uuid4())
            claimed = sql(f"UPDATE labeling_jobs SET status='RUNNING',lease_token='{token}',lease_until=now()+interval '120 seconds' WHERE id='{job}' AND status='QUEUED' RETURNING id")
            if job in claimed:
                response = worker('/jobs/' + job + '/complete', {'leaseToken': token, 'modelDigest': 'fixture-only',
                                  'result': {'answer': '검증', 'labels': ['person'], 'boxes': None}})
                self.assertEqual(response[0], 200, response)
        current = wait(lambda: (v := self.view(pipeline))['pipeline']['stage'] == 'DATASET' and v, 120)
        self.assertEqual(current['pipeline']['labeledCount'], 2)
        self.assertIn('로보플로우', current['pipeline']['message'])
        self.assertIsNone(current['run'])
        sql(f"UPDATE external_datasets SET status='FAILED',error='검증용 실패' WHERE id='{external}'")
        failed = wait(lambda: (v := self.view(pipeline))['pipeline']['stage'] == 'FAILED' and v, 30)
        self.assertIn('검증용 실패', failed['pipeline']['message'])
        self.assertEqual(self.admin.call('POST', f'/training/pipelines/{pipeline}/cancel', {}, self.csrf)[0], 409)

    def test_cancel(self):
        status, view = self.create({'name': '검증 취소', 'sourceKind': 'IOT',
            'source': {'deviceIds': [self.fixture.id], 'targetCount': 50}, 'external': [],
            'options': {'baseModel': 'default', 'epochs': 1, 'confidence': 0.6}})
        pipeline = view['pipeline']['id']
        self.assertEqual(self.admin.call('POST', f'/training/pipelines/{pipeline}/cancel', {}, self.csrf), (200, {'cancelled': True}))
        self.assertEqual(self.view(pipeline)['pipeline']['stage'], 'CANCELLED')

    def test_delete(self):
        status, view = self.create({'name': '검증 삭제', 'sourceKind': 'IOT',
            'source': {'deviceIds': [self.fixture.id], 'targetCount': 50}, 'external': [],
            'options': {'baseModel': 'default', 'epochs': 1, 'confidence': 0.6}})
        pipeline = view['pipeline']['id']
        self.assertEqual(self.admin.call('DELETE', f'/training/pipelines/{pipeline}', None, self.csrf), (409, {'error': 'PIPELINE_ACTIVE'}))
        self.assertEqual(self.admin.call('POST', f'/training/pipelines/{pipeline}/cancel', {}, self.csrf)[0], 200)
        self.assertEqual(self.admin.call('DELETE', f'/training/pipelines/{pipeline}')[0], 403)
        self.assertEqual(self.admin.call('DELETE', f'/training/pipelines/{pipeline}', None, self.csrf)[0], 204)
        self.assertEqual(self.admin.call('DELETE', f'/training/pipelines/{pipeline}', None, self.csrf), (404, {'error': 'PIPELINE_NOT_FOUND'}))
        # 끝난 학습 실행이 달린 파이프라인은 작업 기록·실행·실행 폴더까지 지우고, 등록된 모델이 있으면 거절한다.
        from local_fixtures import ROOT
        for registered in (False, True):
            run, task, pipe = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
            self.pipelines.append(pipe)
            self.addCleanup(lambda run=run: sql(f"DELETE FROM pipelines WHERE run_id='{run}'; DELETE FROM model_releases WHERE id='{run}'; DELETE FROM training_tasks WHERE run_id='{run}'; DELETE FROM training_runs WHERE id='{run}'"))
            sql(f"""INSERT INTO training_runs(id,name,state,snapshot) VALUES('{run}','검증 삭제','FAILED','[]');
                    INSERT INTO training_tasks(id,run_id,action,status,parameters) VALUES('{task}','{run}','TRAIN','FAILED','{{}}');
                    INSERT INTO pipelines(id,name,source_kind,source,options,stage,run_id,created_by)
                    VALUES('{pipe}','검증 삭제','EXISTING','{{}}','{{}}','RUN','{run}','verify')""")
            if registered:
                sql(f"INSERT INTO model_releases(id,sha256,size_bytes) VALUES('{run}','{'0' * 64}',1)")
                self.assertEqual(self.admin.call('DELETE', f'/training/pipelines/{pipe}', None, self.csrf), (409, {'error': 'MODEL_REGISTERED'}))
                continue
            folder = ROOT / '.artifacts/training-runs' / run
            (folder / 'training').mkdir(parents=True)
            (folder / 'training/results.csv').write_text('epoch\n1\n')
            self.addCleanup(lambda folder=folder: __import__('shutil').rmtree(folder, ignore_errors=True))
            self.assertEqual(self.admin.call('DELETE', f'/training/pipelines/{pipe}', None, self.csrf)[0], 204)
            self.assertEqual(sql(f"SELECT count(*) FROM training_runs WHERE id='{run}'").strip(), '0')
            self.assertEqual(sql(f"SELECT count(*) FROM training_tasks WHERE run_id='{run}'").strip(), '0')
            self.assertFalse(folder.exists())

    def test_stop_and_metrics(self):
        # 워커가 가져가지 않도록 이미 임대 중인 가짜 학습 작업으로 중단 API를 검증한다.
        self.assertEqual(sql("SELECT count(*) FROM training_tasks WHERE status IN ('QUEUED','RUNNING')").strip(), '0', '진행 중인 실제 학습이 없을 때 실행하세요.')
        run, task = str(uuid.uuid4()), str(uuid.uuid4())
        self.addCleanup(lambda: sql(f"DELETE FROM training_tasks WHERE run_id='{run}'; DELETE FROM training_runs WHERE id='{run}'"))
        sql(f"""INSERT INTO training_runs(id,name,state,snapshot) VALUES('{run}','검증 중단','TRAINING','[]');
                INSERT INTO training_tasks(id,run_id,action,status,parameters,lease_token,lease_until)
                VALUES('{task}','{run}','TRAIN','RUNNING','{{}}',gen_random_uuid(),now()+interval '1 hour')""")
        self.assertEqual(self.admin.call('POST', f'/training/runs/{run}/stop', {})[0], 403)
        status, stopped = self.admin.call('POST', f'/training/runs/{run}/stop', {}, self.csrf)
        self.assertEqual((status, stopped['state']), (200, 'FAILED'))
        import json
        summary = json.loads(stopped['summary'])
        self.assertEqual((summary['stopped'], summary['failedAction']), (True, 'TRAIN'))
        self.assertEqual(sql(f"SELECT status FROM training_tasks WHERE id='{task}'").strip(), 'FAILED')
        self.assertEqual(self.admin.call('POST', f'/training/runs/{run}/stop', {}, self.csrf), (409, {'error': 'RUN_NOT_ACTIVE'}))
        # 학습 결과가 없는 실행은 빈 지표, 실제 학습 기록이 있는 실행은 results.csv 열·행을 돌려준다.
        self.assertEqual(self.admin.call('GET', f'/training/runs/{run}/metrics'), (200, {'columns': [], 'rows': []}))
        from local_fixtures import ROOT
        recorded = next((p.parents[2].name for p in (ROOT / '.artifacts/training-runs').glob('*/training/*/results.csv')
                         if len(p.parents[2].name) == 36), None)
        if recorded:
            metrics = self.admin.call('GET', f'/training/runs/{recorded}/metrics')[1]
            self.assertIn('metrics/mAP50-95(B)', metrics['columns'])
            self.assertTrue(metrics['rows'] and len(metrics['rows'][0]) == len(metrics['columns']))

    def test_roboflow_source(self):
        options = {'baseModel': 'default', 'epochs': 1, 'confidence': 0.6}
        body = {'name': '검증 로보플로우 전용', 'sourceKind': 'ROBOFLOW', 'source': {}, 'external': [], 'options': options}
        self.assertEqual(self.create(body), (400, {'error': 'ROBOFLOW_SOURCE_REQUIRED'}))
        external = str(uuid.uuid4()); self.externals.append(external)
        sql(f"""INSERT INTO external_datasets(id,url,workspace,project,version,status,lease_token,lease_until)
                VALUES('{external}','https://universe.roboflow.com/verify/only','verify','only',1,'RUNNING',gen_random_uuid(),now()+interval '1 hour')""")
        status, view = self.create({**body, 'external': [{'id': external}]})
        self.assertEqual(status, 202)
        # 수집·라벨링을 건너뛰고 데이터셋 단계에서 외부 데이터셋을 기다린다.
        self.assertEqual((view['pipeline']['stage'], view['pipeline']['sampleCount']), ('DATASET', 0))
        self.assertIn('로보플로우', view['pipeline']['message'])

    def test_gemma_naming(self):
        # 학습 실행을 만들기 전(외부 데이터셋 대기 중)에도 실행 중인 워커가 Gemma로 이름을 먼저 붙인다.
        external = str(uuid.uuid4()); self.externals.append(external)
        sql(f"""INSERT INTO external_datasets(id,url,workspace,project,version,status,error)
                VALUES('{external}','https://universe.roboflow.com/verify/naming','verify','naming',1,'READY','')""")
        sql(f"UPDATE external_datasets SET status='FAILED',error='검증용 실패' WHERE id='{external}'")
        status, view = self.create({'name': '무시되는 이름', 'sourceKind': 'ROBOFLOW', 'source': {}, 'external': [{'id': external}],
                                    'options': {'baseModel': 'yolo26n', 'epochs': 3, 'confidence': 0.6}})
        self.assertEqual(status, 409)
        sql(f"UPDATE external_datasets SET status='RUNNING',lease_token=gen_random_uuid(),lease_until=now()+interval '1 hour' WHERE id='{external}'")
        status, view = self.create({'sourceKind': 'ROBOFLOW', 'source': {}, 'external': [{'id': external}],
                                    'options': {'baseModel': 'yolo26n', 'epochs': 3, 'confidence': 0.6}})
        self.assertEqual((status, view['pipeline']['name']), (202, '이름 생성 대기'))
        self.assertIn(view['pipeline']['nameStatus'], ('PENDING', 'GENERATING'))
        pipeline = view['pipeline']['id']
        named = wait(lambda: (v := self.view(pipeline))['pipeline']['nameStatus'] == 'DONE' and v, 180)
        self.assertEqual(named['pipeline']['stage'], 'DATASET')
        self.assertIsNone(named['run'])
        sql(f"UPDATE external_datasets SET status='FAILED',error='검증용 실패' WHERE id='{external}'")
        self.assertTrue(named, 'AI 워커·Ollama 실행 여부를 확인하세요.')
        self.assertNotEqual(named['pipeline']['name'], '이름 생성 대기')
        self.assertLessEqual(len(named['pipeline']['name']), 53)
        self.assertRegex(named['pipeline']['name'], r'_\d{8}( #\d+)?$')
        print('Gemma 추천 이름:', named['pipeline']['name'])

    def test_external_progress_and_delete(self):
        running, failed = str(uuid.uuid4()), str(uuid.uuid4())
        token = str(uuid.uuid4())
        self.externals += [running, failed]
        sql(f"""INSERT INTO external_datasets(id,url,workspace,project,version,status,lease_token,lease_until)
                VALUES('{running}','https://universe.roboflow.com/verify/progress','verify','progress',1,'RUNNING','{token}',now()+interval '1 hour');
                INSERT INTO external_datasets(id,url,workspace,project,version,status,error)
                VALUES('{failed}','https://universe.roboflow.com/verify/failed','verify','failed',1,'FAILED','검증용 실패')""")
        body = {'leaseToken': token, 'phase': 'DOWNLOAD', 'progress': 42}
        self.assertEqual(worker(f'/external/{running}/heartbeat', body)[0], 200)
        self.assertEqual(worker(f'/external/{running}/heartbeat', {**body, 'progress': 101})[0], 400)
        self.assertEqual(worker(f'/external/{running}/heartbeat', {**body, 'leaseToken': str(uuid.uuid4())})[0], 409)
        item = next(e for e in self.admin.call('GET', '/training/external-datasets')[1] if e['id'] == running)
        self.assertEqual((item['phase'], item['progress']), ('DOWNLOAD', 42))
        self.assertEqual(self.admin.call('DELETE', f'/training/external-datasets/{failed}')[0], 403)
        self.assertEqual(self.admin.call('DELETE', f'/training/external-datasets/{running}', None, self.csrf), (409, {'error': 'ONLY_FAILED_DELETABLE'}))
        self.assertEqual(self.admin.call('DELETE', f'/training/external-datasets/{failed}', None, self.csrf)[0], 204)
        self.assertEqual(self.admin.call('DELETE', f'/training/external-datasets/{failed}', None, self.csrf)[0], 404)

    def claim_and_complete(self, sample_id, labels):
        job = str(uuid.UUID(self.admin.call('GET', '/training/label-jobs?sampleId=' + sample_id)[1][0]['id']))
        token = str(uuid.uuid4())
        claimed = sql(f"UPDATE labeling_jobs SET status='RUNNING',lease_token='{token}',lease_until=now()+interval '120 seconds' WHERE id='{job}' AND status='QUEUED' RETURNING id")
        if job not in claimed:
            return False
        response = worker('/jobs/' + job + '/complete', {'leaseToken': token, 'modelDigest': 'fixture-only',
                          'result': {'answer': '검증', 'labels': labels, 'boxes': None}})
        self.assertEqual(response[0], 200, response)
        return True

    def test_empty_image_discarded(self):
        from local_fixtures import DATA
        sample = upload(self.fixture)[1]
        self.addCleanup(lambda: sql(f"DELETE FROM discarded_samples WHERE id='{uuid.UUID(sample['id'])}'"))
        if not self.claim_and_complete(sample['id'], []):
            self.skipTest('실행 중인 워커가 먼저 처리했습니다.')
        self.assertEqual(self.admin.call('GET', '/training/samples/' + sample['id'])[0], 404)
        self.assertEqual(sql(f"SELECT reason||'|'||source||'|'||device_id FROM discarded_samples WHERE id='{uuid.UUID(sample['id'])}'").strip(),
                         f'NO_OBJECTS|IOT|{self.fixture.id}')
        self.assertFalse((DATA / 'media' / (sample['id'] + '.jpg')).exists())
        self.assertEqual(sql(f"SELECT count(*) FROM labeling_jobs WHERE sample_id='{uuid.UUID(sample['id'])}'").strip(), '0')

    def test_auto_pipeline(self):
        original = self.admin.call('GET', '/training/auto-pipeline')[1]['settings']
        self.addCleanup(lambda: self.admin.call('PUT', '/training/auto-pipeline', {**{k: original[k] for k in ('enabled', 'minSamples', 'epochs', 'basePolicy')}, 'runAt': original['runAt'][:5]}, self.csrf))
        bad = {'enabled': True, 'runAt': '25:00', 'minSamples': 10, 'epochs': 1, 'basePolicy': 'LATEST'}
        self.assertEqual(self.admin.call('PUT', '/training/auto-pipeline', bad, self.csrf)[0], 400)
        self.assertEqual(self.admin.call('PUT', '/training/auto-pipeline', {**bad, 'runAt': '03:30'})[0], 403)
        status, updated = self.admin.call('PUT', '/training/auto-pipeline', {**bad, 'runAt': '03:30'}, self.csrf)
        self.assertEqual((status, updated['settings']['runAt'][:5], updated['settings']['epochs']), (200, '03:30', 1))
        self.assertIn('T', updated['nextRunAt'])
        if updated['pending'] == 0:
            self.assertEqual(self.admin.call('POST', '/training/auto-pipeline/run', {}, self.csrf), (409, {'error': 'NO_NEW_IMAGES'}))
        # 태그 있는 IoT 이미지 1장을 만들어 '지금 실행'이 AUTO 파이프라인을 만드는지 확인하고, 학습 전에 바로 취소한다.
        sample = upload(self.fixture)[1]
        if not self.claim_and_complete(sample['id'], ['person']):
            self.skipTest('실행 중인 워커가 먼저 처리했습니다.')
        self.assertGreaterEqual(self.admin.call('GET', '/training/auto-pipeline')[1]['pending'], 1)
        if sql("SELECT count(*) FROM training_tasks WHERE status IN ('QUEUED','RUNNING')").strip() != '0':
            self.skipTest('진행 중인 학습이 있습니다.')
        status, view = self.admin.call('POST', '/training/auto-pipeline/run', {}, self.csrf)
        self.assertEqual(status, 202, view)
        pipeline = view['pipeline']['id']; self.pipelines.append(pipeline)
        self.assertEqual(self.admin.call('POST', f'/training/pipelines/{pipeline}/cancel', {}, self.csrf)[0], 200)
        import json
        self.assertEqual(view['pipeline']['sourceKind'], 'AUTO')
        self.assertEqual(json.loads(view['pipeline']['options'])['epochs'], 1)
        self.assertEqual(self.admin.call('POST', '/training/auto-pipeline/run', {}, self.csrf)[0] in (202, 409), True)
        for extra in [v['pipeline']['id'] for v in self.admin.call('GET', '/training/pipelines')[1] if v['pipeline']['sourceKind'] == 'AUTO' and v['pipeline']['id'] not in self.pipelines and v['pipeline']['stage'] in ('COLLECTING', 'DATASET')]:
            self.admin.call('POST', f'/training/pipelines/{extra}/cancel', {}, self.csrf); self.pipelines.append(extra)


if __name__ == '__main__':
    unittest.main(verbosity=2)
