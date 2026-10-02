"""Gemma 자동 작업 조회와 작업별 재시작을 검증한다."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import types
import sys
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('training_cli', Path(__file__).resolve().parents[1] / 'scripts/train-test-video.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class AutomaticAnalysisTest(unittest.TestCase):
    def test_existing_job_and_missing_job_use_current_contract(self):
        calls = []
        jobs = {'first': 'existing'}

        class Server:
            def open(self, req, timeout):
                url = req if isinstance(req, str) else req.full_url
                calls.append(url)
                if url.endswith('/csrf'):
                    result = {'headerName': 'X-CSRF-TOKEN', 'token': 'test-only'}
                elif url.endswith('/second/retry-label'):
                    self_test.assertEqual(req.get_method(), 'POST')
                    jobs['second'] = 'new'
                    result = {'id': 'new'}
                else:
                    sample = url.split('sampleId=')[1]
                    result = [] if sample not in jobs else [{
                        'id': jobs[sample], 'kind': 'AUTO_LABEL', 'status': 'COMPLETED', 'model': 'test',
                        'result': json.dumps({'modelDigest': 'digest', 'result': {'labels': []}})}]
                return io.BytesIO(json.dumps(result).encode())

        self_test = self
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            (run / 'run.json').write_text(json.dumps({'sampleIds': ['first', 'second']}))
            with patch.object(cli, 'admin_session', return_value=Server()):
                cli.analyze(run, 'http://test', 40, 1)
                cli.analyze(run, 'http://test', 40, 1)
            state = json.loads((run / 'gemma.json').read_text())
            self.assertEqual(set(state['items']), {'first', 'second'})
            self.assertEqual(sum(url.endswith('/retry-label') for url in calls), 1)
            self.assertFalse(any('analysis-jobs' in url for url in calls))

    def test_partial_registration_is_saved_before_next_request_fails(self):
        class Server:
            def open(self, req, timeout):
                url = req if isinstance(req, str) else req.full_url
                if url.endswith('/csrf'):
                    result = {'headerName': 'X-CSRF-TOKEN', 'token': 'test-only'}
                elif url.endswith('/first/retry-label'):
                    result = {'id': 'persisted-job'}
                elif url.endswith('/second/retry-label'):
                    raise ConnectionError('연결 중단 모의')
                else:
                    result = []
                return io.BytesIO(json.dumps(result).encode())

        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            (run / 'run.json').write_text(json.dumps({'sampleIds': ['first', 'second']}))
            with patch.object(cli, 'admin_session', return_value=Server()):
                with self.assertRaises(ConnectionError):
                    cli.analyze(run, 'http://test', 40, 1)
            state = json.loads((run / 'gemma.json').read_text())
            self.assertEqual(state['jobs'], {'first': 'persisted-job'})

    def test_training_rejects_changed_teacher(self):
        with tempfile.TemporaryDirectory() as folder:
            run=Path(folder)
            teacher=run/'teacher.pt';teacher.write_bytes(b'changed-teacher')
            (run/'selection.json').write_text(json.dumps({'modelSha256':'0'*64}))
            with patch.object(cli,'BASE_MODEL',teacher), patch.object(cli,'prepare_dataset',return_value=run/'dataset.yaml'), patch.dict(sys.modules,{'ultralytics':types.SimpleNamespace(YOLO=lambda _: self.fail('변경된 모델을 로드함'))}):
                with self.assertRaisesRegex(RuntimeError,'기준 모델이 변경'):
                    cli.train(run,1,'cpu',None,0.0001)

    def test_evaluation_rejects_changed_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            run=Path(folder)
            weights=run/'candidate.pt';weights.write_bytes(b'changed-candidate')
            (run/'combined-dataset').mkdir()
            (run/'combined-dataset/dataset.yaml').write_text('test: images/test')
            (run/'model.json').write_text(json.dumps({'weights':str(weights),'sha256':'0'*64}))
            with patch.dict(sys.modules,{'ultralytics':types.SimpleNamespace(YOLO=lambda _: self.fail('변경된 모델을 평가함')), 'yaml':types.SimpleNamespace(safe_load=lambda _: {'test':'images/test'})}):
                with self.assertRaisesRegex(RuntimeError,'후보 모델 파일이 변경'):
                    cli.evaluate(run,'cpu')


if __name__ == '__main__':
    unittest.main()
