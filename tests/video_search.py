"""얇은 한 바퀴: 탐지 배치 → 구간 분석(observer 워커·Gemma) → 조건 검색 → 자연어 질문 대화를 실제 서버로 검증한다.

실행 중인 observer 워커와 Ollama gemma4:12b가 필요하다(약 2분).
`.venv/bin/python tests/video_search.py`. 검증 장치·녹화·대화는 종료 시 삭제한다.
"""
from datetime import datetime, timedelta, timezone
import importlib.util
import time
from urllib import error, parse, request
import json
import unittest
import shutil
import uuid

from integration import BASE, Session
from local_fixtures import DATA, ROOT, DeviceFixture, sql

VIDEO = ROOT.parent / 'iot-server/assets/test-video.mp4'


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / file)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def worker_post(path, body):
    from dev import environment
    req = request.Request('http://127.0.0.1:8201/internal/ai' + path, data=json.dumps(body).encode(), method='POST', headers={
        'Authorization': 'Bearer ' + environment()['AI_WORKER_TOKEN'], 'Content-Type': 'application/json'})
    try:
        with request.urlopen(req, timeout=10) as response:
            return response.status, response.read()
    except error.HTTPError as exc:
        return exc.code, exc.read()


@unittest.skipUnless(VIDEO.exists() and importlib.util.find_spec('ultralytics'), '테스트 영상·ultralytics 필요(pipeline venv)')
class VideoSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = DeviceFixture()
        cls.conversations = []
        cls.addClassCleanup(cls.cleanup)
        cls.camera = 'test-video'
        cls.start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
        iot = {'Authorization': 'Bearer ' + cls.fixture.token}
        assert Session().call('POST', '/iot/register', {'deviceId': cls.fixture.id, 'name': '일시 검증 장치'}, iot)[0] == 200
        registrar = load('register_recording', 'register-recording.py')
        code, cls.recording = registrar.register(VIDEO, cls.fixture.id, cls.camera, cls.start.isoformat(),
                                                 registrar.probe(VIDEO)['durationSeconds'])
        assert code == 200, cls.recording
        cls.sim = load('simulate_video_iot', 'simulate-video-iot.py')
        cls.batches = cls.sim.batches(cls.sim.analyze(), cls.fixture.id, cls.camera, cls.start, uuid.uuid4())
        for batch in cls.batches:
            assert cls.sim.send(batch, cls.fixture.token)[0] == 200
            assert all(c == 200 for c in cls.sim.send_snapshots(batch, cls.fixture.token, cls.start))
        began = time.monotonic()
        while time.monotonic() - began < 600 and cls.statuses().get('QUEUED', 0) + cls.statuses().get('RUNNING', 0):
            time.sleep(2)
        cls.admin = Session(); assert cls.admin.login()[0] == 200

    @classmethod
    def statuses(cls):
        rows = sql(f"SELECT status,count(*) FROM observations WHERE device_id='{cls.fixture.id}' GROUP BY status").splitlines()
        return {s: int(n) for s, n in (row.split('|') for row in rows)}

    @classmethod
    def cleanup(cls):
        for conversation in cls.conversations:
            sql(f"DELETE FROM search_conversations WHERE id='{uuid.UUID(conversation)}'")
        ids = sql(f"SELECT id::text FROM recordings WHERE device_id='{cls.fixture.id}'").split()
        batches = sql(f"SELECT batch_id::text FROM detection_batches WHERE device_id='{cls.fixture.id}'").split()
        cls.fixture.cleanup()  # 탐지 배치·구간 분석·녹화 행은 장치 삭제로 함께 지워진다.
        for recording in ids:
            (DATA / 'media/recordings' / (str(uuid.UUID(recording)) + '.mp4')).unlink(missing_ok=True)
        for batch in batches:
            (DATA / 'media/observations' / (str(uuid.UUID(batch)) + '.jpg')).unlink(missing_ok=True)
            shutil.rmtree(DATA / 'media/detection-snapshots' / str(uuid.UUID(batch)), ignore_errors=True)

    def search(self, **params):
        query = parse.urlencode({'deviceId': self.fixture.id, 'from': self.start.isoformat(),
                                 'to': (self.start + timedelta(minutes=2)).isoformat(), **params})
        return self.admin.call('GET', '/observations/search?' + query)

    def ask(self, message, conversation=None):
        body = {'message': message, **({'conversationId': conversation} if conversation else {})}
        code, question = self.admin.call('POST', '/search/questions', body, self.admin.csrf())
        self.assertEqual(code, 200, question)
        if question['conversationId'] not in self.conversations:
            self.conversations.append(question['conversationId'])
        return question

    def wait(self, question):
        for _ in range(240):
            code, question = self.admin.call('GET', '/search/questions/' + question['id'])
            if question['status'] in ('COMPLETED', 'FAILED'):
                return question
            time.sleep(0.5)
        self.fail('질문 응답 시간 초과')

    def test_1_segments_analyzed_once(self):
        # 마지막 80–84.45초 로고 구간만 생략(사람 없음 또는 어두움), 나머지는 분석 완료. 재전송해도 작업이 늘지 않는다.
        self.assertEqual(self.statuses(), {'COMPLETED': 8, 'SKIPPED': 1})
        for batch in self.batches:
            self.assertTrue(self.sim.send(batch, self.fixture.token)[1]['duplicate'])
            self.assertTrue(all(c == 200 for c in self.sim.send_snapshots(batch, self.fixture.token, self.start)))
        self.assertEqual(self.statuses(), {'COMPLETED': 8, 'SKIPPED': 1})
        # 원본 프레임을 보냈으므로 모든 완료 구간은 녹화(오버레이)가 아니라 원본 프레임으로 판정했다.
        self.assertEqual(sql(f"""SELECT string_agg(DISTINCT coalesce(frame_source,'-'),',') FROM observations
            WHERE device_id='{self.fixture.id}' AND status='COMPLETED'""").strip(), 'SNAPSHOT')
        # 장면 집계(segment-v3): 완료 구간마다 집계가 있고, 의심자는 구간당 3명 이하·실제 추적 ID만.
        counts = sql(f"""SELECT count(*) FILTER (WHERE people IS NULL),sum(people),sum(helmet_absent),sum(workers_no_helmet),
            round(avg(elapsed_ms)),max(elapsed_ms) FROM observations WHERE device_id='{self.fixture.id}' AND status='COMPLETED'""")
        missing, people, absent, workers, avg_ms, max_ms = counts.strip().split('|')
        persons = sql(f"""SELECT count(*),coalesce(max(n),0),
            count(*) FILTER (WHERE NOT known) FROM (SELECT p.batch_id,count(*) OVER (PARTITION BY p.batch_id) AS n,
              p.track_id IN (SELECT track_id FROM detection_tracks t WHERE t.batch_id=p.batch_id) AS known
            FROM observation_persons p JOIN observations o USING (batch_id) WHERE o.device_id='{self.fixture.id}') x""")
        suspects, most, unknown_refs = map(int, persons.split('|'))
        print(f'장면 집계: 사람 합 {people}, 안전모 미착용 합 {absent}(작업자 {workers}), 의심자 {suspects}명, '
              f'구간 처리 평균 {avg_ms}ms·최대 {max_ms}ms')
        self.assertEqual(int(missing), 0)
        self.assertGreater(int(people), 0)
        self.assertLessEqual(most, 3)
        self.assertEqual(unknown_refs, 0)
        # 끝난 작업에 임대 없이 결과를 보내면 거부
        batch = self.batches[0]['batchId']
        self.assertEqual(worker_post(f'/observations/{batch}/complete', {
            'leaseToken': str(uuid.uuid4()), 'status': 'SKIPPED', 'persons': [], 'model': 'x', 'promptVersion': 'x'})[0], 409)

    def test_2_search_and_playback(self):
        code, results = self.search(helmet='absent')
        self.assertEqual(code, 200, results)
        for segment in results:
            # 장면 집계로 찾는다. 강조하는 사람(의심자)은 있을 수도 없을 수도 있지만, 있으면 조건에 맞는다.
            self.assertGreater(segment['counts']['helmetAbsent'], 0)
            self.assertTrue(all(p['helmet'] == 'absent' for p in segment['persons'] if p['trackId'] in segment['matched']))
            # 재생 위치 = 구간 시작 - 녹화 시작(10초 단위)
            expected = (datetime.fromisoformat(segment['startedAt']) - self.start).total_seconds()
            self.assertEqual(segment['recordingId'], self.recording['id'])
            self.assertAlmostEqual(segment['offsetSeconds'], expected, places=6)
            self.assertAlmostEqual(expected % 10, 0, places=6)
        both = self.search(helmet='absent', vest='present')[1]
        self.assertLessEqual({s['batchId'] for s in both}, {s['batchId'] for s in results})
        self.assertTrue(all(s['counts']['workersNoHelmet'] > 0 for s in both))
        self.assertEqual(len(self.search()[1]), 8)
        # 대표 이미지: 분석 완료 구간마다 JPEG, 비로그인 401
        everything = self.search()[1]
        self.assertTrue(all(s['thumbnailPath'] for s in everything))
        req = request.Request(BASE + everything[0]['thumbnailPath'])
        with self.admin.opener.open(req, timeout=10) as response:
            self.assertEqual(response.headers['Content-Type'], 'image/jpeg')
            self.assertEqual(response.read()[:3], b'\xff\xd8\xff')
        self.assertEqual(Session().call('GET', everything[0]['thumbnailPath'][4:])[0], 401)
        self.assertEqual(self.search(text='%')[1], [])  # LIKE 특수문자는 글자 그대로 찾는다.
        self.assertEqual(self.search(helmet='maybe')[0], 400)
        self.assertEqual(self.search(**{'from': self.start.isoformat(), 'to': self.start.isoformat()})[0], 400)
        # 기간을 주지 않으면 전체 기간이다. 30일보다 오래된 구간도 나온다.
        old = everything[0]['batchId']
        shift = f"WHERE batch_id='{uuid.UUID(old)}'"
        sql(f"UPDATE observations SET started_at=started_at-interval '60 days',ended_at=ended_at-interval '60 days' {shift}")
        try:
            code, found = self.admin.call('GET', '/observations/search?' + parse.urlencode({'deviceId': self.fixture.id}))
            self.assertEqual(code, 200)
            self.assertEqual(len(found), len(everything))
            self.assertEqual(found[-1]['batchId'], old)  # 최신순이므로 가장 오래된 구간이 마지막
        finally:
            sql(f"UPDATE observations SET started_at=started_at+interval '60 days',ended_at=ended_at+interval '60 days' {shift}")
        self.assertEqual(Session().call('GET', '/observations/search')[0], 401)

    def test_3_conversation(self):
        self.assertIn(Session().call('POST', '/search/questions', {'message': '안녕'})[0], (401, 403))  # 비로그인·CSRF 없음
        self.assertEqual(self.admin.call('POST', '/search/questions', {'message': 'x' * 501}, self.admin.csrf())[0], 400)
        self.assertEqual(self.admin.call('POST', '/search/questions', {'message': '질문', 'conversationId': str(uuid.uuid4())},
                                         self.admin.csrf())[0], 404)
        first = self.ask('오늘 안전모 안 쓴 사람 나온 장면 찾아줘')
        # 답을 기다리는 동안 같은 대화에 또 물으면 거부
        self.assertEqual(self.admin.call('POST', '/search/questions', {'message': '그중에 조끼 입은 사람만',
                                         'conversationId': first['conversationId']}, self.admin.csrf())[0], 409)
        first = self.wait(first)
        self.assertEqual(first['status'], 'COMPLETED', first)
        self.assertEqual(first['filter']['helmet'], 'absent')
        self.assertEqual(first['trace']['output']['helmet'], 'absent')  # 중간 단계에 보여 줄 Gemma 원본 출력
        self.assertEqual(first['trace']['message'], first['message'])
        mine = [s for s in first['results'] if s['deviceId'] == self.fixture.id]
        self.assertEqual({s['batchId'] for s in mine}, {s['batchId'] for s in self.search(helmet='absent')[1]})

        follow = self.wait(self.ask('그중에 조끼는 입은 사람만', first['conversationId']))
        self.assertEqual(follow['status'], 'COMPLETED', follow)
        self.assertEqual((follow['filter']['helmet'], follow['filter']['vest']), ('absent', 'present'))
        self.assertEqual(follow['seq'], 2)

        scene = self.wait(self.ask('모니터 앞에 앉아 있는 장면 있어?'))
        self.assertEqual(scene['status'], 'COMPLETED', scene)
        self.assertIsNone(scene['filter']['helmet'])
        self.assertIn('모니터', scene['filter']['text'] or '')
        # 날짜·시간 표현이 없는 새 질문은 전체 기간을 찾는다.
        self.assertEqual((scene['filter']['from'], scene['filter']['to']), (None, None))
        self.assertTrue(first['filter']['from'] and first['filter']['to'])  # "오늘"은 기간을 만든다.
        for q in (first, follow, scene):
            print(f"{q['message']} → {q['elapsedMs']}ms {json.dumps(q['filter'], ensure_ascii=False)} 결과 {len(q['results'])}")
        # 생성 중지: 답을 기다리는 질문만. 중지 뒤 워커의 늦은 답은 저장되지 않는다.
        stopped = self.ask('어제 오후 3시쯤 조끼 안 입은 사람')
        code, canceled = self.admin.call('POST', f"/search/questions/{stopped['id']}/cancel", {}, self.admin.csrf())
        self.assertEqual((code, canceled['status']), (200, 'CANCELED'))
        self.assertEqual(self.admin.call('POST', f"/search/questions/{stopped['id']}/cancel", {}, self.admin.csrf())[0], 409)
        time.sleep(6)
        self.assertEqual(self.admin.call('GET', '/search/questions/' + stopped['id'])[1]['status'], 'CANCELED')
        history = self.admin.call('GET', '/search/conversations/' + first['conversationId'])[1]
        self.assertEqual([q['seq'] for q in history], [1, 2])


if __name__ == '__main__':
    unittest.main(verbosity=2)
