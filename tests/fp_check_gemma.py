"""오탐 확인 끝단 검증: 2026-10-07 웹캠 원본(IoT 수집 이미지)을 검증 장치의 사람 감지 전송처럼 보내고 실제 Gemma로 판정한다.

현장 모델(ac8fe811) 검출을 배치로 보내고, 워커 fp_check를 그대로 실행한다. 데이터셋은 임시 폴더에 쓰고 검증 장치·파일은 지운다.
기대: 대머리 안전모·베이지 셔츠 조끼는 false(또는 unsure), 남색 공항 조끼는 real. Gemma 응답이라 결과가 바뀔 수 있어
판정 표를 출력하고, 남색 조끼가 false로 확정되는 경우(잘못된 오탐 확정)만 실패로 본다.
실행: CENTRAL_TEST_URL·CENTRAL_INTERNAL_URL을 맞추고 .venv/bin/python tests/fp_check_gemma.py (Ollama gemma4:12b 필요)
"""
from datetime import datetime, timedelta, timezone
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'ai-worker')]
from integration import BASE, Session  # noqa: E402
from local_fixtures import DATA, DeviceFixture, sql  # noqa: E402
from detection_snapshots import raw  # noqa: E402
import fp_check  # noqa: E402
import observer  # noqa: E402
from worker import api  # noqa: E402

MODEL = ROOT / '.artifacts/training-runs/ac8fe811-f745-42fc-a7ab-354f8f1b1d19/release/model.pt'
IMAGES = {'대머리·베이지 셔츠(쪼그려 앉음)': '908d77d5-f599-48ae-aff7-809928383219',
          '대머리·베이지 셔츠(숙임)': '13bf8f2f-35fb-4591-8de5-d9df1a36f255',
          '남색 공항 조끼': 'ed91371c-b57b-41cc-8bf8-43d54ff110d7'}


def main():
    from ultralytics import YOLO
    model = YOLO(str(MODEL))
    fixture = DeviceFixture()
    temp = Path(tempfile.mkdtemp(prefix='gemma-fp-verify-'))
    fp_check.DATASET = temp / 'gemma-fp'
    batches, failed = [], False
    try:
        assert Session().call('POST', '/iot/register', {'deviceId': fixture.id, 'name': '일시 검증 장치'},
                              {'Authorization': 'Bearer ' + fixture.token})[0] == 200
        start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
        for i, (label, sample) in enumerate(IMAGES.items()):
            path = DATA / 'media' / (sample + '.jpg')
            result = model.predict(str(path), conf=0.25, imgsz=640, device='mps', verbose=False)[0]
            objects = [{'cls': model.names[int(b.cls)], 'score': round(float(b.conf), 3),
                        'box': [round(min(1, max(0, v)), 4) for v in b.xyxyn[0].tolist()]} for b in result.boxes]
            at = (start + timedelta(seconds=i)).isoformat(timespec='microseconds')
            body = {'batchId': str(uuid.uuid4()), 'deviceId': fixture.id, 'cameraId': 'usb-0', 'sessionId': str(uuid.uuid4()),
                    'startedAt': at, 'endedAt': (start + timedelta(seconds=i, milliseconds=1)).isoformat(timespec='microseconds'),
                    'model': 'ac8fe811', 'tracker': 'bytetrack', 'cuts': [], 'frames': [{'at': at, 'objects': objects}]}
            assert Session().call('POST', '/iot/detections', body, {'Authorization': 'Bearer ' + fixture.token})[0] == 200
            batches.append(body['batchId'])
            boundary = 'snap-' + uuid.uuid4().hex
            data = b''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                            for k, v in {'batchId': body['batchId'], 'deviceId': fixture.id, 'at': at}.items())
            data += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="f.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'
                     ).encode() + path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
            assert raw('POST', BASE + '/api/iot/detection-snapshots', data, {'Content-Type': 'multipart/form-data; boundary=' + boundary,
                       'Authorization': 'Bearer ' + fixture.token})[0] == 200
            job = api('/fp-checks/claim?deviceId=' + fixture.id, {})
            print(f'\n[{label}] YOLO:', ', '.join(f"{o['cls']} {o['score']}" for o in objects))
            fp_check.check(job, observer.gemma, observer.OllamaDown)
        admin = Session()
        admin.login()
        view = admin.call('GET', '/training/fp-checks?limit=50')[1]
        mine = [item for item in view['items'] if item['deviceId'] == fixture.id]
        print('\n판정 결과')
        for item in sorted(mine, key=lambda x: x['capturedAt']):
            label = list(IMAGES)[batches.index(next(b for b in batches if True)) if False else 0]
            verdicts = json.loads(item['verdicts'])
            print(' ', item['capturedAt'], item['datasetSplit'] or item['skipReason'],
                  '; '.join(f"{v['cls']}={v['verdict']}({v['seen']})" for v in verdicts))
        navy = sorted(mine, key=lambda x: x['capturedAt'])[-1]
        if any(v['cls'] == 'safety_vest' and v['verdict'] == 'false' for v in json.loads(navy['verdicts'])):
            print('실패: 남색 공항 조끼를 오탐으로 확정함')
            failed = True
        files = sorted(p.relative_to(temp) for p in temp.rglob('*') if p.is_file())
        print('임시 데이터셋 파일:', *files, sep='\n  ')
        for label_file in temp.rglob('labels/*/*.txt'):
            print(' ', label_file.name, '→', label_file.read_text().strip().replace('\n', ' | ') or '(빈 라벨)')
    finally:
        fixture.cleanup()
        # 검증이 임시 폴더로 만든 외부 데이터셋 행을 지운다(실제 행은 경로가 임시 폴더가 아니다).
        sql(f"DELETE FROM external_datasets WHERE url='local:gemma-fp' AND yaml_path LIKE '{temp}%'")
        for batch in batches:
            shutil.rmtree(DATA / 'media/detection-snapshots' / batch, ignore_errors=True)
        shutil.rmtree(temp, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
