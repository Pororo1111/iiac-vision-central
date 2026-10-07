"""오탐 검토 시연 데이터: 2026-10-07 웹캠 이미지를 시연 장치의 '사람 감지 원본 전송'처럼 중앙에 보낸다.

setup  : 시연 장치(demo-fp-review)를 만들고, 오늘 IoT 수집 원본 8장과 오버레이 없는 송출 녹화 프레임(Claude 검토 목록)을
         현장 모델(ac8fe811) 검출과 함께 프레임 1개 배치 + 원본 JPEG로 보낸다. 보호구 검출이 있는 프레임은 오탐 확인 대기열에 들어가고
         observer가 Gemma로 판정한 뒤 오탐·애매가 있으면 영상 검색의 '관리자 확인이 필요한 사진'에 나온다.
cleanup: 시연 장치와 배치·원본·오탐 확인 기록, 시연 사진으로 승인된 데이터셋 파일을 지우고 데이터셋 수를 다시 맞춘다.
         (승인한 시연 사진을 학습 자료로 남기려면 cleanup을 실행하지 않는다.)

실행: .venv/bin/python scripts/fp-review-demo.py setup|cleanup   (CENTRAL_TEST_URL로 대상 변경, 기본 http://localhost:8200)
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from dev import DATA, PG, environment  # noqa: E402

DEVICE = 'demo-fp-review'
CAMERA = 'usb-0'
BASE = os.environ.get('CENTRAL_TEST_URL', 'http://localhost:8200')
STATE = ROOT / '.artifacts' / 'fp-review-demo.json'
MODEL = ROOT / '.artifacts/training-runs/ac8fe811-f745-42fc-a7ab-354f8f1b1d19/release/model.pt'
WORK = ROOT / '.artifacts/claude-labels-20261007'
DATASET = ROOT / '.artifacts/ai-assets/datasets/admin-fp'
KST = timezone(timedelta(hours=9))


def sql(statement):
    return subprocess.check_output([str(PG / 'psql'), '-v', 'ON_ERROR_STOP=1', '-Atc', statement], env=environment(), text=True).strip()


def http(method, path, body=None, headers=None):
    req = request.Request(BASE + path, data=body, method=method, headers=headers or {})
    try:
        with request.urlopen(req, timeout=30) as response:
            return response.status, response.read()
    except error.HTTPError as exc:
        return exc.code, exc.read()


def frames():
    """(촬영 시각, JPEG 바이트) 목록. 원본 수집 이미지는 수집 시각, 녹화 프레임은 파일 시작 시각 + 프레임 번호/5fps."""
    out = []
    rows = sql("SELECT id, to_char(captured_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"') FROM training_samples "
               "WHERE device_id='iot-jetson-001' AND camera_id='usb-0' "
               "AND created_at >= '2026-10-07 00:00+09' AND created_at < '2026-10-08 00:00+09' ORDER BY captured_at")
    for line in rows.splitlines():
        sample, at = line.split('|')
        path = DATA / 'media' / (sample + '.jpg')
        if path.exists():
            out.append((datetime.fromisoformat(at), path.read_bytes()))
    from PIL import Image
    final = json.loads((WORK / 'final.json').read_text())
    for key in sorted(final):
        if key.startswith('sample_'):
            continue
        stamp, index = key.rsplit('_', 1)
        start = datetime.strptime(stamp, '%Y-%m-%d_%H-%M-%S-%f').replace(tzinfo=KST)
        data = io.BytesIO()
        Image.open(WORK / 'frames' / (key + '.png')).convert('RGB').save(data, 'JPEG', quality=90)
        out.append((start + timedelta(seconds=int(index) / 5), data.getvalue()))
    return sorted(out)


def setup():
    if STATE.exists():
        raise SystemExit('이미 설정되어 있습니다. 먼저 cleanup을 실행하세요.')
    from ultralytics import YOLO
    model = YOLO(str(MODEL))
    token = secrets.token_hex(32)
    sql(f"INSERT INTO devices(device_id,name,token_hash) VALUES('{DEVICE}','오탐 검토 시연 장치','{hashlib.sha256(token.encode()).hexdigest()}')")
    auth = {'Authorization': 'Bearer ' + token}
    status, body = http('POST', '/api/iot/register', json.dumps({'deviceId': DEVICE, 'name': '오탐 검토 시연 장치'}).encode(),
                        {**auth, 'Content-Type': 'application/json'})
    assert status == 200, body
    batches, sent, queued = [], 0, 0
    STATE.write_text(json.dumps({'batches': batches}))
    for at, jpeg in frames():
        result = model.predict(__import__('PIL.Image', fromlist=['Image']).open(io.BytesIO(jpeg)), conf=0.35, imgsz=640, device='mps', verbose=False)[0]
        objects = [{'cls': model.names[int(b.cls)], 'score': round(float(b.conf), 3),
                    'box': [round(min(1, max(0, v)), 4) for v in b.xyxyn[0].tolist()]} for b in result.boxes]
        if not any(o['cls'] == 'person' for o in objects):
            continue  # IoT는 사람이 검출된 프레임만 보낸다.
        when = at.astimezone(timezone.utc).isoformat(timespec='microseconds')
        session = str(uuid.uuid4())
        batch = {'batchId': str(uuid.uuid5(uuid.UUID(session), when)), 'deviceId': DEVICE, 'cameraId': CAMERA, 'sessionId': session,
                 'startedAt': when, 'endedAt': (at + timedelta(milliseconds=1)).astimezone(timezone.utc).isoformat(timespec='microseconds'),
                 'model': 'ac8fe811', 'tracker': 'none', 'cuts': [], 'frames': [{'at': when, 'objects': objects}]}
        status, body = http('POST', '/api/iot/detections', json.dumps(batch).encode(), {**auth, 'Content-Type': 'application/json'})
        assert status == 200, body
        batches.append(batch['batchId'])
        STATE.write_text(json.dumps({'batches': batches}))
        boundary = 'demo-' + uuid.uuid4().hex
        data = b''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                        for k, v in {'batchId': batch['batchId'], 'deviceId': DEVICE, 'at': when}.items())
        data += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="f.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'
                 ).encode() + jpeg + f'\r\n--{boundary}--\r\n'.encode()
        status, body = http('POST', '/api/iot/detection-snapshots', data, {**auth, 'Content-Type': 'multipart/form-data; boundary=' + boundary})
        assert status == 200, body
        sent += 1
        queued += any(o['cls'] in ('hard_hat', 'safety_vest') for o in objects)
        print(at.astimezone(KST).strftime('%H:%M:%S'), ', '.join(f"{o['cls']} {o['score']}" for o in objects if o['cls'] != 'person'))
    print(f'보낸 프레임 {sent}개, 오탐 확인 대상 {queued}개. observer가 Gemma로 판정하면 영상 검색에서 "관리자 확인이 필요한 사진 있어?"로 볼 수 있습니다.')


def cleanup():
    batches = json.loads(STATE.read_text())['batches'] if STATE.exists() else []
    names = sql(f"SELECT dataset_split || '/' || dataset_name FROM fp_checks WHERE device_id='{DEVICE}' AND dataset_name IS NOT NULL")
    for item in filter(None, names.splitlines()):
        split, name = item.split('/')
        (DATASET / 'images' / split / name).unlink(missing_ok=True)
        (DATASET / 'labels' / split / name.replace('.jpg', '.txt')).unlink(missing_ok=True)
    sql(f"DELETE FROM devices WHERE device_id='{DEVICE}'")
    for batch in batches:
        shutil.rmtree(DATA / 'media/detection-snapshots' / batch, ignore_errors=True)
        (DATA / 'media/observations' / (batch + '.jpg')).unlink(missing_ok=True)
    # 승인 기록이 지워졌으므로 데이터셋 수를 다시 맞추고, 남은 승인이 train·val을 모두 갖추지 못하면 외부 데이터셋 행을 지운다.
    sql("""UPDATE external_datasets SET counts=jsonb_build_object(
             'train',(SELECT count(*) FROM fp_checks WHERE review_status='APPROVED' AND dataset_split='train'),
             'val',(SELECT count(*) FROM fp_checks WHERE review_status='APPROVED' AND dataset_split='val'),'test',0,
             'boxes',(SELECT coalesce(sum(dataset_boxes),0) FROM fp_checks WHERE review_status='APPROVED'))
           WHERE url='local:admin-fp'""")
    sql("""DELETE FROM external_datasets WHERE url='local:admin-fp' AND NOT EXISTS(
             SELECT 1 FROM fp_checks WHERE review_status='APPROVED' AND dataset_split='val') AND NOT EXISTS(
             SELECT 1 FROM pipelines p WHERE p.external::text LIKE '%' || external_datasets.id::text || '%')""")
    STATE.unlink(missing_ok=True)
    print(f'시연 장치·배치 {len(batches)}개·관련 파일을 지웠습니다.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['setup', 'cleanup'])
    {'setup': setup, 'cleanup': cleanup}[parser.parse_args().command]()
