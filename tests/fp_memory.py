"""오탐 판단 기억(V37, RAG) 계약을 실제 PostgreSQL(pgvector)·실행 중인 중앙으로 검증한다. CLIP·Gemma는 부르지 않는다.

- 관리자 승인 시 보호구 박스마다 기억이 생긴다(오탐으로 뺀 박스 false, 남긴 박스 real, 임베딩은 비어 있음). 반려는 기억하지 않는다.
- 워커는 임베딩이 빈 기억을 받아 512차원 벡터와 확대 사진을 채운다. 잘못된 벡터·사진은 400이다.
- 기본 사례(seed)는 origin으로 갱신되고, 검색은 같은 클래스에서 코사인 유사도 순으로 돌려준다.
- 워커 완료는 판정 근거(method·match)를 받아 그대로 저장한다. 근거 사진은 관리자 세션으로만 본다.

실행: .venv/bin/python tests/fp_memory.py (CENTRAL_TEST_URL·CENTRAL_INTERNAL_URL로 대상 변경). 검증 장치·기억·사진은 종료 시 지운다.
"""
import base64
import io
import json
import random
import sys
import unittest
import uuid
from pathlib import Path
from urllib import request

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detection_snapshots import person, raw  # noqa: E402
import fp_checks  # noqa: E402
from fp_checks import HAT, INTERNAL, VEST, WORKER, sql, worker  # noqa: E402
from integration import BASE, Session  # noqa: E402
from local_fixtures import DATA  # noqa: E402

CROPS = DATA / 'media' / 'fp-memory'


def unit(seed):
    rng = random.Random(seed)
    values = [rng.gauss(0, 1) for _ in range(512)]
    norm = sum(v * v for v in values) ** 0.5
    return [round(v / norm, 7) for v in values]


def crop():
    data = io.BytesIO()
    Image.new('RGB', (32, 32), 'beige').save(data, 'JPEG')
    return base64.b64encode(data.getvalue()).decode()


class FpMemory(fp_checks.FpChecks):
    # 오탐 확인 자체의 계약은 tests/fp_checks.py가 검증한다. 여기서는 준비·정리 도구만 물려받는다.
    test_enqueue_only_ppe_frames = test_completion_sets_review_status = test_approve_writes_dataset_and_registers = None
    test_reject_and_admin_views = test_worker_auth_and_lease = None

    def setUp(self):
        super().setUp()
        self.origins = []
        self.addCleanup(self.forget)

    def forget(self):
        ids = sql(f"""SELECT id FROM fp_memory WHERE fp_check_id IN (SELECT id FROM fp_checks WHERE device_id='{self.fixture.id}')
                      OR origin IN ({','.join(f"'{o}'" for o in self.origins) or "''"})""")
        for memory in filter(None, ids.splitlines()):
            (CROPS / f'{memory}.jpg').unlink(missing_ok=True)
        if self.origins:
            sql(f"DELETE FROM fp_memory WHERE origin IN ({','.join(f"'{o}'" for o in self.origins)})")

    def memories(self, check):
        return sql(f"SELECT box_index, cls, label, source, created_by, embedding IS NULL FROM fp_memory "
                   f"WHERE fp_check_id='{check}' ORDER BY box_index").splitlines()

    def test_approval_remembers_and_worker_embeds(self):
        check = self.judged([person(), HAT, VEST], [{'index': 1, 'verdict': 'false', 'seen': '대머리', 'reason': ''},
                                                    {'index': 2, 'verdict': 'unsure', 'seen': '', 'reason': ''}], 0)
        rejected = self.judged([person(), VEST], [{'index': 1, 'verdict': 'false', 'seen': '', 'reason': ''}], 1)
        self.assertEqual(self.review(check, {'action': 'APPROVE', 'removed': [1]})[0], 200)
        self.assertEqual(self.review(rejected, {'action': 'REJECT'})[0], 200)
        login = sql(f"SELECT reviewed_by FROM fp_checks WHERE id='{check}'")
        self.assertEqual(self.memories(check), [f'1|hard_hat|false|ADMIN|{login}|t', f'2|safety_vest|real|ADMIN|{login}|t'])
        self.assertEqual(self.memories(rejected), [])

        code, pending = self.pending()
        self.assertEqual(code, 200)
        mine = {p['id']: p for p in pending if p['id'] in sql(f"SELECT string_agg(id::text, ',') FROM fp_memory WHERE fp_check_id='{check}'")}
        self.assertEqual(sorted(p['cls'] for p in mine.values()), ['hard_hat', 'safety_vest'])
        hat = next(p for p in mine.values() if p['cls'] == 'hard_hat')
        self.assertEqual(hat['box'], HAT['box'])
        self.assertTrue(Path(hat['imagePath']).is_file())

        body = {'vector': unit(1), 'model': 'verify-clip', 'crop': crop()}
        self.assertEqual(worker(f"/fp-memory/{hat['id']}/embedding", {**body, 'vector': unit(1)[:511]})[0], 400)
        self.assertEqual(worker(f"/fp-memory/{hat['id']}/embedding", {**body, 'crop': base64.b64encode(b'not jpeg').decode()})[0], 400)
        self.assertEqual(worker(f"/fp-memory/{uuid.uuid4()}/embedding", body)[0], 404)
        self.assertEqual(worker(f"/fp-memory/{hat['id']}/embedding", body)[0], 200)
        self.assertEqual(sql(f"SELECT embedding IS NULL, embed_model FROM fp_memory WHERE id='{hat['id']}'"), 'f|verify-clip')
        self.assertTrue((CROPS / f"{hat['id']}.jpg").is_file())
        self.assertNotIn(hat['id'], [p['id'] for p in self.pending()[1]])

        path = f"/api/training/fp-memory/{hat['id']}/image"
        with self.admin.opener.open(request.Request(BASE + path), timeout=10) as response:
            self.assertEqual(response.headers['Content-Type'], 'image/jpeg')
        self.assertEqual(Session().call('GET', f"/training/fp-memory/{hat['id']}/image")[0], 401)
        code, view = self.admin.call('GET', '/training/fp-checks?limit=1')
        self.assertEqual(code, 200)
        self.assertGreaterEqual(view['memory']['admin'], 2)

    def pending(self):
        return raw('GET', INTERNAL + '/fp-memory/pending', None, WORKER)

    def test_seed_and_search(self):
        origin = 'verify-' + uuid.uuid4().hex
        self.origins.append(origin)
        seed = {'origin': origin, 'cls': 'safety_vest', 'label': 'false', 'box': [0.1, 0.2, 0.5, 0.9], 'note': '검증 셔츠',
                'vector': unit(2), 'model': 'verify-clip', 'crop': crop()}
        code, first = worker('/fp-memory/seed', seed)
        self.assertEqual(code, 200, first)
        code, again = worker('/fp-memory/seed', {**seed, 'label': 'real'})
        self.assertEqual((code, again['id']), (200, first['id']))
        self.assertEqual(sql(f"SELECT source, label, note FROM fp_memory WHERE origin='{origin}'"), 'CLAUDE|real|검증 셔츠')
        self.assertEqual(worker('/fp-memory/seed', {**seed, 'cls': 'person'})[0], 400)
        self.assertEqual(worker('/fp-memory/seed', {**seed, 'box': [0.5, 0.2, 0.1, 0.9]})[0], 400)

        code, found = worker('/fp-memory/search', {'cls': 'safety_vest', 'vector': unit(2), 'k': 3})
        self.assertEqual(code, 200, found)
        self.assertEqual(found[0]['id'], first['id'])
        self.assertAlmostEqual(found[0]['similarity'], 1.0, places=4)
        self.assertEqual((found[0]['source'], found[0]['label']), ('CLAUDE', 'real'))
        self.assertTrue(Path(found[0]['cropPath']).is_file())
        self.assertLessEqual(max(sum(m['label'] == label for m in found) for label in ('real', 'false')), 3)  # 판단별 k개
        code, hats = worker('/fp-memory/search', {'cls': 'hard_hat', 'vector': unit(2)})
        self.assertNotIn(first['id'], [m['id'] for m in hats])
        self.assertEqual(worker('/fp-memory/search', {'cls': 'safety_vest', 'vector': unit(2), 'k': 50})[0], 400)

    def test_completion_keeps_basis(self):
        self.send([person(), VEST], 0)
        job = self.claim()
        match = {'id': str(uuid.uuid4()), 'source': 'ADMIN', 'label': 'false', 'similarity': 0.93, 'opposite': 0.71}
        verdict = {'index': 1, 'verdict': 'false', 'seen': '관리자 판단 사례와 비슷함', 'reason': '', 'method': 'memory', 'match': match}
        base = {'leaseToken': job['leaseToken'], 'model': 'gemma4:12b', 'promptVersion': 'fp-v5', 'elapsedMs': 10}
        self.assertEqual(worker(f"/fp-checks/{job['id']}/complete", {**base, 'verdicts': [{**verdict, 'method': 'guess'}]})[0], 400)
        self.assertEqual(worker(f"/fp-checks/{job['id']}/complete", {**base, 'verdicts': [verdict]})[0], 200)
        stored = json.loads(sql(f"SELECT verdicts FROM fp_checks WHERE id='{job['id']}'"))[0]
        self.assertEqual((stored['method'], stored['match']), ('memory', match))


if __name__ == '__main__':
    unittest.main()
