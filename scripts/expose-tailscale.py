"""기존 Serve 설정을 유지하며 중앙 서버를 tailnet HTTPS 9443에 연결한다."""
import json
import os
from pathlib import Path
import subprocess
from simulate_iot import config

ROOT = Path(__file__).resolve().parents[1]
SERVE_PORT = 9443

def run(*args):
    return subprocess.run(args, cwd=ROOT, check=True)

def read_json(*args):
    return json.loads(subprocess.check_output(args, cwd=ROOT, text=True))

status = read_json('tailscale', 'status', '--json')
if status.get('BackendState') != 'Running':
    raise SystemExit('먼저 Tailscale에 로그인하고 연결하세요.')
name = status['Self']['DNSName'].rstrip('.')
env = config()
port = int(env.get('CENTRAL_PORT', '8200'))
if not 1 <= port <= 65535:
    raise SystemExit('CENTRAL_PORT 설정을 확인하세요.')
target = f'http://127.0.0.1:{port}'
serve = read_json('tailscale', 'serve', 'status', '--json')
key = f'{name}:{SERVE_PORT}'
existing = serve.get('Web', {}).get(key)
if str(SERVE_PORT) in serve.get('TCP', {}):
    if existing != {'Handlers': {'/': {'Proxy': target}}}:
        raise SystemExit('9443 포트에 다른 서비스가 있습니다. 기존 서비스를 덮어쓰지 않습니다.')
if serve.get('AllowFunnel', {}).get(key):
    raise SystemExit('9443 포트의 Funnel 공개 설정을 먼저 해제해야 합니다.')
path = ROOT / '.env'
lines = path.read_text().splitlines()
lines = [line for line in lines if not line.startswith('COOKIE_SECURE=')]
path.write_text('\n'.join([*lines, 'COOKIE_SECURE=true']) + '\n')
# 셸 환경이 .env보다 우선하지 않도록 Compose 실행에 true를 명시한다.
os.environ['COOKIE_SECURE'] = 'true'
run('docker', 'compose', 'up', '-d', '--wait', 'backend', 'nginx')
run('docker', 'compose', 'exec', '-T', 'nginx', 'nginx', '-t')
run('docker', 'compose', 'exec', '-T', 'nginx', 'nginx', '-s', 'reload')
run('tailscale', 'serve', '--bg', f'--https={SERVE_PORT}', target)
after = read_json('tailscale', 'serve', 'status', '--json')
for old_key, old_value in serve.get('Web', {}).items():
    if old_key != key and after.get('Web', {}).get(old_key) != old_value:
        raise SystemExit('기존 Serve 설정에 차이가 있습니다. tailscale serve status로 확인하세요.')
if after.get('AllowFunnel', {}).get(key):
    raise SystemExit('예상과 다른 공개 설정이 감지되었습니다.')
print(f'중앙 서버: https://{key}/login (tailnet only)')
