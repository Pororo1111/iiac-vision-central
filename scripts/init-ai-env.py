"""기존 자격 증명을 유지하고 AI 워커 전용 토큰만 추가한다."""
from pathlib import Path
import os
import secrets

path = Path(__file__).resolve().parents[1] / '.env'
if not path.exists():
    raise SystemExit('먼저 python3 scripts/init-env.py를 실행하세요.')
lines = path.read_text().splitlines()
if any(line.startswith('AI_WORKER_TOKEN=') and len(line.partition('=')[2]) >= 32 for line in lines):
    print('기존 AI 워커 토큰을 유지합니다.')
else:
    lines = [line for line in lines if not line.startswith('AI_WORKER_TOKEN=')]
    lines.append('AI_WORKER_TOKEN=' + secrets.token_urlsafe(32))
    with path.open('w') as stream:
        stream.write('\n'.join(lines) + '\n')
    os.chmod(path, 0o600)
    print('AI 워커 전용 토큰을 추가했습니다. 값은 출력하지 않습니다.')
