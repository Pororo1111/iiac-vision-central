"""관리자 수동 입력(이미지·영상 파일·유튜브 링크)을 실행 중인 로컬 서버·실제 PostgreSQL·AI 워커로 검증한다.

영상 파일 검증은 실행 중인 AI 워커가 프레임을 추출해야 통과한다. 유튜브는 실제 내려받기 없이
주소 검증·정규화만 확인하고 등록 직후 작업을 취소한다. 생성한 데이터와 파일은 종료 시 제거한다.
"""
import io
import json
import shutil
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from urllib import error, request

import cv2
import numpy as np
from PIL import Image
from integration import Session, BASE
from local_fixtures import sql, DATA


def image(color):
    data = io.BytesIO()
    Image.new('RGB', (96, 64), color).save(data, 'PNG')
    return data.getvalue()


def test_video(seconds=6):
    """초마다 색이 바뀌는 짧은 MP4. 유사 프레임 생략에 걸리지 않도록 매초 크게 달라진다."""
    path = Path(tempfile.mkdtemp()) / 'clip.mp4'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 10, (160, 120))
    for frame in range(seconds * 10):
        second = frame // 10
        writer.write(np.full((120, 160, 3), ((second * 40) % 256, 255 - second * 40, (second * 90) % 256), np.uint8))
    writer.release()
    data = path.read_bytes()
    shutil.rmtree(path.parent)
    return data


def multipart(session, path, files, fields=None, headers=None):
    boundary = uuid.uuid4().hex
    body = bytearray()
    for key, value in (fields or {}).items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
    for key, name, data in files:
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{name}"\r\n\r\n'.encode() + data + b'\r\n'
    body += f'--{boundary}--\r\n'.encode()
    req = request.Request(BASE + '/api' + path, data=bytes(body), method='POST',
                          headers={**(headers or {}), 'Content-Type': 'multipart/form-data; boundary=' + boundary})
    try:
        response = session.opener.open(req, timeout=60)
    except error.HTTPError as exc:
        response = exc
    with response:
        data = response.read()
        return response.status, json.loads(data) if data else None


class ManualUpload(unittest.TestCase):
    def setUp(self):
        self.admin = Session(); self.assertEqual(self.admin.login()[0], 200)
        self.csrf = self.admin.csrf()
        self.samples, self.imports = [], []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        ids = set(self.samples)
        for item in self.imports:
            ids.update(sql(f"SELECT id::text FROM training_samples WHERE import_id='{uuid.UUID(item)}'").split())
        quoted = ','.join(f"'{uuid.UUID(i)}'" for i in ids) or 'NULL'
        imports = ','.join(f"'{uuid.UUID(i)}'" for i in self.imports) or 'NULL'
        sql(f"""BEGIN;
          DELETE FROM annotation_reviews WHERE sample_id IN ({quoted});
          DELETE FROM labeling_jobs WHERE sample_id IN ({quoted});
          DELETE FROM training_samples WHERE id IN ({quoted});
          DELETE FROM media_imports WHERE id IN ({imports}); COMMIT;""")
        for sample in ids:
            (DATA / 'media' / (sample + '.jpg')).unlink(missing_ok=True)
        for item in self.imports:
            (DATA / 'media' / 'imports' / (item + '.video')).unlink(missing_ok=True)

    def test_images(self):
        files = [('files', 'red.png', image((255, 0, 0))), ('files', 'blue.png', image((0, 0, 255)))]
        anonymous = Session()
        self.assertEqual(multipart(anonymous, '/training/uploads', files, headers=anonymous.csrf())[0], 401)
        self.assertEqual(multipart(self.admin, '/training/uploads', files)[0], 403)
        status, saved = multipart(self.admin, '/training/uploads', files, headers=self.csrf)
        self.assertEqual(status, 200)
        self.samples += [s['id'] for s in saved]
        self.assertEqual([s['source'] for s in saved], ['UPLOAD', 'UPLOAD'])
        self.assertEqual([s['title'] for s in saved], ['red.png', 'blue.png'])
        self.assertIsNone(saved[0]['deviceId'])
        jobs = self.admin.call('GET', '/training/label-jobs?sampleId=' + saved[0]['id'])[1]
        self.assertEqual([j['kind'] for j in jobs], ['AUTO_LABEL'])
        # 같은 이미지를 다시 올리면 새 샘플·작업을 만들지 않는다.
        status, again = multipart(self.admin, '/training/uploads', files[:1], headers=self.csrf)
        self.assertEqual((status, again[0]['id']), (200, saved[0]['id']))
        self.assertEqual(len(self.admin.call('GET', '/training/label-jobs?sampleId=' + saved[0]['id'])[1]), 1)
        self.assertEqual(multipart(self.admin, '/training/uploads', [('files', 'x.png', b'not image')], headers=self.csrf),
                         (400, {'error': 'INVALID_IMAGE'}))
        self.assertEqual(multipart(self.admin, '/training/uploads', files * 11, headers=self.csrf),
                         (400, {'error': 'INVALID_FILE_COUNT'}))

    def test_youtube_validation(self):
        call = lambda url, **extra: self.admin.call('POST', '/training/imports/youtube',
                                                     {'url': url, 'intervalSeconds': 2, 'maxFrames': 5, **extra}, self.csrf)
        for url in ['https://example.com/watch?v=abcdefghijk', 'https://www.youtube.com/watch?v=short',
                    'file:///etc/passwd', 'https://www.youtube.com/playlist?list=PL123', 'javascript:alert(1)']:
            self.assertEqual(call(url), (400, {'error': 'INVALID_YOUTUBE_URL'}), url)
        self.assertEqual(call('https://youtu.be/abcdefghijk', maxFrames=500)[0], 400)
        self.assertEqual(self.admin.call('POST', '/training/imports/youtube',
                                         {'url': 'https://youtu.be/abcdefghijk', 'intervalSeconds': 2, 'maxFrames': 5})[0], 403)
        # 존재하지 않는 영상 ID이며, 워커가 가져가기 전에 곧바로 취소한다.
        status, created = call('https://www.youtube.com/watch?v=abcdefghijk&list=PL1&t=30s')
        self.assertEqual(status, 202)
        self.imports.append(created['id'])
        sql(f"UPDATE media_imports SET status='FAILED',error='검증용 취소',finished_at=now() WHERE id='{uuid.UUID(created['id'])}'")
        self.assertEqual(created['source'], 'https://www.youtube.com/watch?v=abcdefghijk')
        self.assertEqual(created['kind'], 'YOUTUBE')
        self.assertIsNone(created['leaseToken'])
        status, shorts = call('https://m.youtube.com/shorts/abcdefghij_')
        self.imports.append(shorts['id'])
        sql(f"UPDATE media_imports SET status='FAILED',error='검증용 취소',finished_at=now() WHERE id='{uuid.UUID(shorts['id'])}'")
        self.assertEqual(shorts['source'], 'https://www.youtube.com/watch?v=abcdefghij_')

    def test_video_file(self):
        bad = multipart(self.admin, '/training/imports/video', [('file', 'fake.mp4', b'0' * 64)], headers=self.csrf)
        self.assertEqual(bad, (400, {'error': 'UNSUPPORTED_VIDEO'}))
        status, created = multipart(self.admin, '/training/imports/video', [('file', 'clip.mp4', test_video())],
                                    {'title': '검증 영상', 'intervalSeconds': '1', 'maxFrames': '4'}, self.csrf)
        self.assertEqual(status, 202)
        self.imports.append(created['id'])
        for _ in range(60):
            current = next(i for i in self.admin.call('GET', '/training/imports')[1] if i['id'] == created['id'])
            if current['status'] in ('COMPLETED', 'FAILED'):
                break
            time.sleep(2)
        self.assertEqual((current['status'], current['error']), ('COMPLETED', None), 'AI 워커 실행 여부를 확인하세요.')
        self.assertEqual(current['frames'], 4)
        frames = sql(f"SELECT source||'|'||title FROM training_samples WHERE import_id='{uuid.UUID(created['id'])}' ORDER BY title").splitlines()
        self.assertEqual(frames, ['VIDEO_FILE|검증 영상 · 00:00', 'VIDEO_FILE|검증 영상 · 00:01',
                                  'VIDEO_FILE|검증 영상 · 00:02', 'VIDEO_FILE|검증 영상 · 00:03'])
        queued = sql(f"SELECT count(*) FROM labeling_jobs j JOIN training_samples s ON s.id=j.sample_id WHERE s.import_id='{uuid.UUID(created['id'])}' AND j.kind='AUTO_LABEL'")
        self.assertEqual(queued.strip(), '4')
        self.assertFalse((DATA / 'media' / 'imports' / (created['id'] + '.video')).exists(), '처리 후 원본 영상 삭제')


if __name__ == '__main__':
    unittest.main(verbosity=2)
