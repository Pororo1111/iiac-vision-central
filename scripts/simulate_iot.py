"""중앙 통신 검증용 모의 장치. 실제 IoT 추론 서버와 독립적이다."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import time
from datetime import datetime, timezone
from urllib import request, error
import uuid

ROOT = Path(__file__).resolve().parents[1]

def config():
    values = {}
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key] = value
    return {**values, **os.environ}

class DeviceClient:
    def __init__(self, base, device_id, token, outbox):
        self.base, self.device_id, self.token = base.rstrip('/'), device_id, token
        Path(outbox).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(outbox)
        self.db.execute('CREATE TABLE IF NOT EXISTS outbox (sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)')
        self.db.commit()

    def post(self, path, payload):
        req = request.Request(self.base + '/api/iot/' + path,
            data=json.dumps(payload).encode(), method='POST',
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token})
        with request.urlopen(req, timeout=5) as response:
            return json.load(response)

    def register(self):
        return self.post('register', {'deviceId': self.device_id, 'name': '통신 검증 장치'})

    def heartbeat(self):
        return self.post('heartbeat', {'deviceId': self.device_id, 'modelName': 'simulator / no camera', 'runtimeStatus': 'RUNNING'})

    def enqueue(self, kind):
        body = {'eventId': str(uuid.uuid4()), 'deviceId': self.device_id, 'cameraId': 'sim-camera',
            'zoneId': 'sim-zone', 'type': kind, 'occurredAt': datetime.now(timezone.utc).isoformat(),
            'objectCount': 1 if kind == 'INTRUSION_START' else 0}
        with self.db:
            self.db.execute('INSERT INTO outbox(event_id,payload) VALUES(?,?)', (body['eventId'], json.dumps(body)))
        return body

    def flush(self):
        sent = 0
        for event_id, payload in self.db.execute('SELECT event_id,payload FROM outbox ORDER BY sequence').fetchall():
            reply = self.post('events', json.loads(payload))
            if not reply.get('accepted') or reply.get('eventId') != event_id:
                raise RuntimeError('중앙의 저장 확인 응답이 올바르지 않습니다.')
            with self.db:
                self.db.execute('DELETE FROM outbox WHERE event_id=?', (event_id,))
            sent += 1
        return sent

    def pending(self):
        return self.db.execute('SELECT count(*) FROM outbox').fetchone()[0]

    def close(self):
        self.db.close()

def main():
    env = config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:' + env.get('CENTRAL_PORT', '8200'))
    parser.add_argument('--outbox', default=str(ROOT / '.artifacts/simulator.db'))
    parser.add_argument('--enqueue', choices=['start', 'end'])
    parser.add_argument('--enqueue-only', action='store_true')
    parser.add_argument('--watch', action='store_true', help='5초 heartbeat 및 실패 시 최대 60초 백오프')
    args = parser.parse_args()
    if not env.get('IOT_DEVICE_TOKEN'):
        parser.error('IOT_DEVICE_TOKEN 설정이 필요합니다.')
    client = DeviceClient(args.url, env.get('IOT_DEVICE_ID', 'iot-001'), env['IOT_DEVICE_TOKEN'], args.outbox)
    try:
        if args.enqueue:
            body = client.enqueue('INTRUSION_START' if args.enqueue == 'start' else 'INTRUSION_END')
            print('로컬 저장:', body['eventId'])
        if args.enqueue_only:
            print('미전송 이벤트:', client.pending())
            return
        delay = 5
        while True:
            try:
                client.register()
                client.heartbeat()
                print('중앙 연결 정상 / 전송:', client.flush(), '/ 대기:', client.pending())
                delay = 5
            except (error.URLError, TimeoutError, RuntimeError) as exc:
                code = getattr(exc, 'code', None)
                print('중앙 전송 실패 / HTTP:', code or '연결 오류', '/ 보관:', client.pending())
                if code and 400 <= code < 500 and code != 429:
                    raise SystemExit('인증 또는 요청 설정 확인이 필요합니다. 큐를 보존하고 종료합니다.')
                if not args.watch:
                    raise SystemExit(1)
                delay = min(delay * 2, 60)
            if not args.watch:
                break
            time.sleep(delay)
    except KeyboardInterrupt:
        print('모의 장치를 종료합니다. 미전송 이벤트는 유지됩니다.')
    finally:
        client.close()

if __name__ == '__main__':
    main()
