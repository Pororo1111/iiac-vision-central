"""개발 backend를 잠시 중단·복구하여 모의 IoT outbox와 오프라인 전이를 검증한다."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib import error
from integration import Session, BASE, ENV
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from simulate_iot import DeviceClient

ROOT = Path(__file__).resolve().parents[1]

def compose(*args):
    subprocess.run(['docker', 'compose', *args], cwd=ROOT, check=True, capture_output=True)

def wait_ready():
    for _ in range(45):
        try:
            if Session().call('GET','/health')[0] == 200:
                return
        except error.URLError:
            pass
        time.sleep(1)
    raise AssertionError('중앙 서버가 제한 시간 안에 복구되지 않았습니다.')

with tempfile.TemporaryDirectory() as directory:
    outbox = Path(directory) / 'outbox.db'
    client = DeviceClient(BASE, ENV['IOT_DEVICE_ID'], ENV['IOT_DEVICE_TOKEN'], outbox)
    client.register()
    client.heartbeat()
    admin = Session()
    assert admin.login()[0] == 200
    assert next(d for d in admin.call('GET','/devices')[1] if d['deviceId'] == client.device_id)['online']
    print('heartbeat 온라인 확인. 31초 후 오프라인 전이를 확인합니다.', flush=True)
    time.sleep(31)
    device = next(d for d in admin.call('GET','/devices')[1] if d['deviceId'] == client.device_id)
    assert not device['online'] and device['runtimeStatus'] == 'RUNNING'
    print('오프라인 전이 및 마지막 실행 상태 분리 확인.', flush=True)
    try:
        compose('stop','backend')
        event = client.enqueue('INTRUSION_START')
        try:
            client.flush()
            raise AssertionError('중단된 중앙으로 전송이 성공해서는 안 됩니다.')
        except (error.URLError, TimeoutError):
            pass
        assert client.pending() == 1
        client.close()
        client = DeviceClient(BASE, ENV['IOT_DEVICE_ID'], ENV['IOT_DEVICE_TOKEN'], outbox)
        assert client.pending() == 1
        print('중앙 중단 및 클라이언트 재시작 후 outbox 보존 확인.', flush=True)
    finally:
        compose('start','backend')
    wait_ready()
    client.register()
    client.heartbeat()
    assert client.flush() == 1 and client.pending() == 0
    assert client.post('events', event)['duplicate'] is True
    assert admin.call('GET','/devices')[0] == 401, '재시작한 서버는 과거 메모리 세션을 허용하면 안 됩니다.'
    assert admin.login()[0] == 200
    status, result = admin.call('GET','/events?size=100')
    assert status == 200
    assert sum(e['eventId'] == event['eventId'] for e in result['items']) == 1
    client.close()
    print('복구 후 재전송·중복 방지·세션 만료 검증 통과.', flush=True)
