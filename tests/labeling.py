"""로컬 실제 PostgreSQL에서 수집·프롬프트 작업을 검증하며 모든 테스트 데이터를 정리한다."""
import concurrent.futures
from datetime import datetime, timezone
import io
import json
import unittest
import uuid
from urllib import request, error
from PIL import Image
from integration import Session, ENV, BASE
from local_fixtures import DeviceFixture, sql


def raw(opener, url, data=None, headers=None):
    try:
        response = opener.open(request.Request(url, data=data, headers=headers or {}), timeout=30)
    except error.HTTPError as exc:
        response = exc
    with response:
        payload = response.read()
        try: payload = json.loads(payload) if payload else None
        except ValueError: pass
        return response.status, payload


def upload(fixture, content=None, device=None, captured=None):
    if content is None:
        out = io.BytesIO(); Image.new('RGB', (64, 64), '#559977').save(out, 'PNG'); content = out.getvalue()
    boundary = 'sample-' + uuid.uuid4().hex
    fields = {'title': '테스트 종료 시 자동 삭제', 'deviceId': device or fixture.id, 'cameraId': 'test-camera',
              'capturedAt': captured or datetime.now(timezone.utc).isoformat()}
    data = b''
    for key, value in fields.items():
        data += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
    data += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="test.png"\r\nContent-Type: image/png\r\n\r\n'.encode() + content + f'\r\n--{boundary}--\r\n'.encode()
    return raw(request.build_opener(), BASE + '/api/iot/training-samples', data, {
        'Authorization': 'Bearer ' + fixture.token, 'Content-Type': 'multipart/form-data; boundary=' + boundary})


def worker(path, body, token=None):
    return raw(request.build_opener(), 'http://127.0.0.1:8201/internal/ai' + path,
        json.dumps(body).encode(), {'Authorization': 'Bearer ' + (token if token is not None else ENV['AI_WORKER_TOKEN']), 'Content-Type': 'application/json'})


class PromptWorkflow(unittest.TestCase):
    def setUp(self):
        self.fixture = DeviceFixture()
        self.addCleanup(self.fixture.cleanup)
        self.admin = Session(); self.assertEqual(self.admin.login()[0], 200)
        self.csrf = self.admin.csrf()
        self.assertEqual(Session().call('POST', '/iot/register', {'deviceId': self.fixture.id, 'name': '일시 검증 장치'}, {'Authorization': 'Bearer ' + self.fixture.token})[0], 200)

    def test_manual_prompt_disabled(self):
        self.assertEqual(Session().call('GET', '/training/stats')[0], 401)
        _, sample = upload(self.fixture)
        body = {'sampleIds': [sample['id']], 'instruction': '수동 요청'}
        self.assertIn(self.admin.call('POST', '/training/analysis-jobs', body, self.csrf)[0], [404,405])
        self.assertIn(self.admin.call('POST', '/training/label-jobs', {'sampleId': sample['id'], 'instruction': '수동 요청'}, self.csrf)[0], [404,405])
        jobs = self.admin.call('GET', '/training/label-jobs?sampleId=' + sample['id'])[1]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['kind'], 'AUTO_LABEL')


if __name__ == '__main__':
    unittest.main(verbosity=2)
