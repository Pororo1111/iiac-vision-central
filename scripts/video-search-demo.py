#!/usr/bin/env python3
"""영상 검색 대화 데모: 테스트 영상을 검증 장치 하나로 등록하고 탐지 배치를 보내 구간 분석을 기다린다.

setup  : 장치 `verify-video-search` 생성·등록 → 원본 녹화 등록 → 모의 IoT 탐지 배치 전송 → observer 워커 분석 대기
cleanup: 장치·녹화 파일·분석 결과·질문 대화를 삭제한다.
실행: .venv/bin/python scripts/video-search-demo.py setup [--started-at <UTC ISO>]
IoT·웹캠·MediaMTX는 사용하지 않는다. 장치 토큰은 메모리에서만 쓰고 출력하지 않는다.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib import request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from dev import DATA, PG, environment

DEVICE = 'verify-video-search'
CAMERA = 'test-video'
VIDEO = ROOT.parent / 'iot-server/assets/test-video.mp4'
KST = timezone(timedelta(hours=9))


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / file)
    loaded = importlib.util.module_from_spec(spec); spec.loader.exec_module(loaded)
    return loaded


def sql(statement):
    return subprocess.check_output([str(PG / 'psql'), '-v', 'ON_ERROR_STOP=1', '-Atc', statement], env=environment(), text=True).strip()


def cleanup():
    recordings = sql(f"SELECT id::text FROM recordings WHERE device_id='{DEVICE}'").split()
    batches = sql(f"SELECT batch_id::text FROM detection_batches WHERE device_id='{DEVICE}'").split()
    # 질문 대화는 장치와 연결되지 않으므로 데모 장치를 만든 뒤의 대화만 지운다.
    sql(f"""BEGIN;
      DELETE FROM search_conversations WHERE created_at >= coalesce((SELECT created_at FROM devices WHERE device_id='{DEVICE}'), now());
      DELETE FROM recordings WHERE device_id='{DEVICE}';
      DELETE FROM devices WHERE device_id='{DEVICE}'; COMMIT;""")
    for recording in recordings:
        (DATA / 'media/recordings' / (str(uuid.UUID(recording)) + '.mp4')).unlink(missing_ok=True)
    for batch in batches:
        (DATA / 'media/observations' / (str(uuid.UUID(batch)) + '.jpg')).unlink(missing_ok=True)
    print('데모 장치·녹화·분석 결과를 삭제했습니다.')


def setup(started):
    cleanup()
    token = secrets.token_hex(32)
    sql(f"INSERT INTO devices(device_id,name,token_hash) VALUES('{DEVICE}','영상 검색 데모','{hashlib.sha256(token.encode()).hexdigest()}')")
    req = request.Request('http://127.0.0.1:8200/api/iot/register', method='POST',
                          data=json.dumps({'deviceId': DEVICE, 'name': '영상 검색 데모'}).encode(),
                          headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    request.urlopen(req, timeout=10).close()

    registrar = module('register_recording', 'register-recording.py')
    info = registrar.probe(VIDEO)
    status, recording = registrar.register(VIDEO, DEVICE, CAMERA, started.isoformat(), info['durationSeconds'])
    if status != 200:
        raise SystemExit(f'녹화 등록 실패: {status} {recording}')

    sim = module('simulate_video_iot', 'simulate-video-iot.py')
    print('YOLO·추적 실행 중…', flush=True)
    analysis = sim.analyze()
    batches = sim.batches(analysis, DEVICE, CAMERA, started, uuid.uuid4())
    for batch in batches:
        code, body = sim.send(batch, token)
        if code != 200:
            raise SystemExit(f'탐지 배치 전송 실패: {code} {body}')
    print(f'탐지 배치 {len(batches)}개 전송, 구간 분석 대기(구간당 약 10초)…', flush=True)
    began = time.monotonic()
    while time.monotonic() - began < 900:
        counts = dict(line.split('|') for line in sql(
            f"SELECT status,count(*) FROM observations WHERE device_id='{DEVICE}' GROUP BY status").splitlines())
        if not counts.get('QUEUED') and not counts.get('RUNNING'):
            break
        time.sleep(3)
    first = started.astimezone(KST)
    last = (started + timedelta(seconds=info['durationSeconds'])).astimezone(KST)
    print(json.dumps({'device': DEVICE, 'camera': CAMERA, 'recording': recording['id'], 'observations': counts,
                      'videoTimeKst': f'{first:%Y-%m-%d %H:%M:%S} ~ {last:%H:%M:%S}',
                      'elapsedSeconds': round(time.monotonic() - began)}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['setup', 'cleanup'])
    parser.add_argument('--started-at', help='UTC ISO 8601 가상 촬영 시작 시각(기본: 5분 전, 초 단위)')
    args = parser.parse_args()
    if args.command == 'cleanup':
        cleanup()
        return
    started = (datetime.fromisoformat(args.started_at) if args.started_at
               else datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5))
    setup(started)


if __name__ == '__main__':
    main()
