"""영상 분석 장애·재처리(P6)와 응답 검증(P3 보강), 지난 대화 목록을 실제 서버·PostgreSQL로 검증한다.

실행 중인 dev observer를 잠시 내리고 테스트가 직접 띄운 observer로 장애를 만든다. 끝나면 `dev.py start`로 원래 observer를 되살린다.
Ollama gemma4:12b가 필요하다(약 3분). `.venv/bin/python tests/observer_recovery.py`. 검증 장치·녹화·대화는 종료 시 삭제한다.
Ollama 자체는 끄지 않는다. 모델 중단은 연결되지 않는 주소(OLLAMA_URL)와 연결 거부를 흉내 낸 응답으로 만든다.
"""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from urllib import error
import uuid

from integration import Session
from local_fixtures import DATA, ROOT, DeviceFixture, sql
from video_search import VIDEO, load, worker_post

sys.path.insert(0, str(ROOT / 'ai-worker'))
PYTHON = str(ROOT / '.venv/bin/python')


def dev_observer_pid():
    from dev import STATE
    return json.loads(STATE.read_text()).get('observer') if STATE.exists() else None


def stop_group(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # 이미 끝난(좀비) 그룹은 macOS에서 PermissionError
        pass


@unittest.skipUnless(VIDEO.exists() and importlib.util.find_spec('ultralytics'), '테스트 영상·ultralytics 필요(.venv)')
class ObserverRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.processes, cls.conversations = [], []
        cls.addClassCleanup(cls.cleanup)
        # 다른 대기 작업이 있으면 테스트 작업과 섞이므로 실행하지 않는다.
        busy = sql("SELECT count(*) FROM observations WHERE status IN ('QUEUED','RUNNING')").strip()
        if busy != '0':
            raise unittest.SkipTest(f'처리 중인 구간 분석 작업 {busy}건이 있어 건너뜀')
        pid = dev_observer_pid()
        if pid:
            stop_group(pid)
            time.sleep(1)
        cls.fixture = DeviceFixture()
        cls.camera = 'test-video'
        cls.start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
        iot = {'Authorization': 'Bearer ' + cls.fixture.token}
        assert Session().call('POST', '/iot/register', {'deviceId': cls.fixture.id, 'name': '일시 검증 장치'}, iot)[0] == 200
        registrar = load('register_recording', 'register-recording.py')
        code, cls.recording = registrar.register(VIDEO, cls.fixture.id, cls.camera, cls.start.isoformat(),
                                                 registrar.probe(VIDEO)['durationSeconds'])
        assert code == 200, cls.recording
        sim = load('simulate_video_iot', 'simulate-video-iot.py')
        cls.batches = sim.batches(sim.analyze(), cls.fixture.id, cls.camera, cls.start, uuid.uuid4())
        for batch in cls.batches:
            assert sim.send(batch, cls.fixture.token)[0] == 200
            assert all(c == 200 for c in sim.send_snapshots(batch, cls.fixture.token, cls.start))
        # 사람이 한 번도 탐지되지 않은 구간(로고 화면)은 받을 때 바로 SKIPPED(no_person)다.
        cls.no_person = sum(1 for b in cls.batches if not sim.snapshot_picks(b))
        cls.admin = Session(); assert cls.admin.login()[0] == 200

    @classmethod
    def cleanup(cls):
        for process, _ in cls.processes:
            stop_group(process.pid)
            process.wait(timeout=10)
        for conversation in cls.conversations:
            sql(f"DELETE FROM search_conversations WHERE id='{uuid.UUID(conversation)}'")
        if hasattr(cls, 'fixture'):
            ids = sql(f"SELECT id::text FROM recordings WHERE device_id='{cls.fixture.id}'").split()
            batches = sql(f"SELECT batch_id::text FROM detection_batches WHERE device_id='{cls.fixture.id}'").split()
            cls.fixture.cleanup()
            for recording in ids:
                (DATA / 'media/recordings' / (str(uuid.UUID(recording)) + '.mp4')).unlink(missing_ok=True)
            for batch in batches:
                (DATA / 'media/observations' / (str(uuid.UUID(batch)) + '.jpg')).unlink(missing_ok=True)
                shutil.rmtree(DATA / 'media/detection-snapshots' / str(uuid.UUID(batch)), ignore_errors=True)
        subprocess.run([sys.executable, str(ROOT / 'scripts/dev.py'), 'start'], stdout=subprocess.DEVNULL, check=False)

    # ---- 도우미 ----
    def rows(self):
        out = sql(f"SELECT batch_id,status,attempts,coalesce(error,'') FROM observations WHERE device_id='{self.fixture.id}' ORDER BY started_at")
        return [dict(zip(('id', 'status', 'attempts', 'error'), r.split('|'))) for r in out.splitlines()]

    def statuses(self):
        counts = {}
        for r in self.rows():
            counts[r['status']] = counts.get(r['status'], 0) + 1
        return counts

    def observer(self, **env):
        log = tempfile.NamedTemporaryFile('w+', suffix='.log', delete=False)
        process = subprocess.Popen([PYTHON, '-u', str(ROOT / 'ai-worker/observer.py')], cwd=ROOT, stdout=log, stderr=log,
                                   env={**os.environ, **env}, start_new_session=True)
        self.processes.append((process, log.name))
        return process, log.name

    def until(self, check, seconds, message):
        began = time.monotonic()
        while time.monotonic() - began < seconds:
            if check():
                return
            time.sleep(1)
        self.fail(message)

    def ask(self, message):
        code, question = self.admin.call('POST', '/search/questions', {'message': message}, self.admin.csrf())
        self.assertEqual(code, 200, question)
        self.conversations.append(question['conversationId'])
        return question

    def question_status(self, question):
        return self.admin.call('GET', '/search/questions/' + question['id'])[1]

    # ---- 시나리오 ----
    def test_1_ollama_down_does_not_consume_jobs(self):
        process, log = self.observer(OLLAMA_URL='http://127.0.0.1:9')
        time.sleep(8)
        self.assertEqual(self.statuses(), {'QUEUED': 9 - self.no_person, **({'SKIPPED': self.no_person} if self.no_person else {})})
        self.assertTrue(all(r['attempts'] == '0' for r in self.rows()))
        self.assertIn('응답 없음', open(log).read())
        # 워커가 질문을 가져가지 않으면 60초 뒤 서버가 실패로 알린다(시간 경과는 created_at을 당겨 흉내 낸다).
        question = self.ask('안전모 안 쓴 사람')
        time.sleep(3)
        self.assertEqual(self.question_status(question)['status'], 'QUEUED')
        sql(f"UPDATE search_questions SET created_at=now()-interval '61 seconds' WHERE id='{uuid.UUID(question['id'])}'")
        self.until(lambda: self.question_status(question)['status'] == 'FAILED', 25, '대기 질문이 실패로 바뀌지 않음')
        self.assertIn('워커가 응답하지 않습니다', self.question_status(question)['error'])
        stop_group(process.pid)

    def test_2_killed_worker_lease_is_swept_by_server(self):
        process, _ = self.observer()
        self.until(lambda: self.statuses().get('RUNNING'), 60, '작업을 가져가지 않음')
        stop_group(process.pid)  # 분석 도중 강제 종료
        time.sleep(2)
        running = [r for r in self.rows() if r['status'] == 'RUNNING']
        self.assertEqual(len(running), 1)
        # 임대 120초 경과를 흉내 낸다. 워커가 없어도 서버의 주기 정리가 대기열로 되돌려야 한다.
        sql(f"UPDATE observations SET lease_until=now()-interval '1 second' WHERE batch_id='{running[0]['id']}'")
        self.until(lambda: next(r for r in self.rows() if r['id'] == running[0]['id'])['status'] == 'QUEUED', 25,
                   '죽은 워커의 작업이 대기열로 돌아오지 않음')
        row = next(r for r in self.rows() if r['id'] == running[0]['id'])
        self.assertEqual((row['attempts'], row['error']), ('1', '워커 응답 시간 초과'))
        # 응답 중 멈춘 질문도 임대가 끝나면 실패로 알린다.
        question = self.ask('조끼 입은 사람')
        sql(f"UPDATE search_questions SET status='RUNNING',lease_token=gen_random_uuid(),lease_until=now()-interval '1 second' "
            f"WHERE id='{uuid.UUID(question['id'])}'")
        self.until(lambda: self.question_status(question)['status'] == 'FAILED', 25, '멈춘 질문이 실패로 바뀌지 않음')
        self.assertIn('시간이 초과', self.question_status(question)['error'])
        # 다음 시나리오가 시도 횟수를 처음부터 세도록 맞춘다.
        sql(f"UPDATE observations SET attempts=0 WHERE device_id='{self.fixture.id}' AND status='QUEUED'")

    def test_3_worker_paths_release_and_invalid_output(self):
        import observer

        def claim():
            code, body = worker_post('/observations/claim', {})
            self.assertEqual(code, 200, body)
            job = json.loads(body)
            self.assertEqual(job['attempt'], 1)
            return job

        original = observer.ollama
        try:
            # 분석 도중 Ollama 연결이 끊기면 시도 횟수를 쓰지 않고 대기열로 되돌린다.
            def refused(*_):
                raise error.URLError(ConnectionRefusedError(61, 'Connection refused'))
            observer.ollama = refused
            job = claim()
            observer.analyze_segment(job)
            row = next(r for r in self.rows() if r['id'] == job['batchId'])
            self.assertEqual((row['status'], row['attempts']), ('QUEUED', '0'))
            self.assertIn('분석 모델 연결 실패', row['error'])

            # 잘못된 JSON은 실패로 기록하고 시도 횟수를 쓴다. 3번째 시도까지 틀리면 FAILED.
            observer.ollama = lambda *_: {'message': {'content': '{"summary": "잘린 응답'}, 'done_reason': 'stop'}
            job = claim()
            observer.analyze_segment(job)
            row = next(r for r in self.rows() if r['id'] == job['batchId'])
            self.assertEqual((row['status'], row['attempts'], row['error']), ('QUEUED', '1', '모델 응답 형식 오류: JSON 아님'))
            sql(f"UPDATE observations SET attempts=2 WHERE batch_id='{job['batchId']}'")
            sql(f"UPDATE observations SET created_at=created_at+interval '1 hour' WHERE device_id='{self.fixture.id}' "
                f"AND batch_id<>'{job['batchId']}'")  # 같은 작업을 다시 가져가도록 순서를 앞으로
            observer.ollama = lambda *_: {'message': {'content': '{}'}, 'done_reason': 'length'}
            again = json.loads(worker_post('/observations/claim', {})[1])
            self.assertEqual((again['batchId'], again['attempt']), (job['batchId'], 3))
            observer.analyze_segment(again)
            row = next(r for r in self.rows() if r['id'] == job['batchId'])
            self.assertEqual((row['status'], row['error']), ('FAILED', '모델 응답 형식 오류: 응답 길이 초과'))
        finally:
            observer.ollama = original

    def test_4_retry_failed(self):
        failed = [r['id'] for r in self.rows() if r['status'] == 'FAILED']
        self.assertEqual(len(failed), 1)
        self.assertIn(Session().call('POST', '/observations/retry-failed')[0], (401, 403))
        self.assertEqual(self.admin.call('POST', '/observations/retry-failed?deviceId=bad%20id', {}, self.admin.csrf())[0], 400)
        code, body = self.admin.call('POST', '/observations/retry-failed?deviceId=' + self.fixture.id, {}, self.admin.csrf())
        self.assertEqual((code, body), (200, {'requeued': 1}))
        row = next(r for r in self.rows() if r['id'] == failed[0])
        self.assertEqual((row['status'], row['attempts'], row['error']), ('QUEUED', '0', ''))

    def test_5_recovered_observer_finishes_everything(self):
        subprocess.run([sys.executable, str(ROOT / 'scripts/dev.py'), 'start'], stdout=subprocess.DEVNULL, check=True)
        self.until(lambda: not ({'QUEUED', 'RUNNING'} & set(self.statuses())), 600, '복구 후 남은 작업이 있음')
        self.assertEqual(self.statuses(), {'COMPLETED': 8, 'SKIPPED': 1})
        # 같은 구간을 여러 번 분석했어도 사람 판정은 한 벌만 남는다(추적 ID당 1행, 모르는 참조 없음).
        dup, unknown = sql(f"""SELECT count(*)-count(DISTINCT (p.batch_id,p.track_id)),
            count(*) FILTER (WHERE p.track_id NOT IN (SELECT track_id FROM detection_tracks t WHERE t.batch_id=p.batch_id))
            FROM observation_persons p JOIN observations o USING (batch_id) WHERE o.device_id='{self.fixture.id}'""").strip().split('|')
        self.assertEqual((dup, unknown), ('0', '0'))
        # P0에서 요약에 새어 나왔던 자막 이름은 저장되지 않는다.
        text = sql(f"""SELECT string_agg(o.summary,' ') || ' ' || coalesce((SELECT string_agg(p.evidence,' ') FROM observation_persons p
            JOIN observations x USING (batch_id) WHERE x.device_id='{self.fixture.id}'),'')
            FROM observations o WHERE o.device_id='{self.fixture.id}'""")
        for name in ('권도현', '이영섭'):
            self.assertNotIn(name, text)
        print('이름 가림 표시 수', text.count('○○○'))
        question = self.ask('모니터 앞에 앉아 있는 장면')
        self.until(lambda: self.question_status(question)['status'] == 'COMPLETED', 120, '복구 후 질문 응답 없음')

    def test_6_conversation_list_and_delete(self):
        self.assertIn(Session().call('GET', '/search/conversations')[0], (401, 403))
        code, listed = self.admin.call('GET', '/search/conversations')
        self.assertEqual(code, 200, listed)
        mine = {c['id']: c for c in listed}
        for conversation in self.conversations:
            self.assertIn(conversation, mine)
            self.assertEqual(mine[conversation]['questions'], 1)
        self.assertEqual(mine[self.conversations[-1]]['title'], '모니터 앞에 앉아 있는 장면')
        self.assertEqual(listed, sorted(listed, key=lambda c: c['updatedAt'], reverse=True))
        target = self.conversations[0]
        self.assertIn(Session().call('DELETE', '/search/conversations/' + target)[0], (401, 403))
        self.assertEqual(self.admin.call('DELETE', '/search/conversations/' + target, None, self.admin.csrf())[0], 204)
        self.assertEqual(self.admin.call('GET', '/search/conversations/' + target)[0], 404)
        self.assertEqual(self.admin.call('DELETE', '/search/conversations/' + target, None, self.admin.csrf())[0], 404)
        self.assertNotIn(target, {c['id'] for c in self.admin.call('GET', '/search/conversations')[1]})


if __name__ == '__main__':
    unittest.main(verbosity=2)
