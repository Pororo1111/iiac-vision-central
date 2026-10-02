"""기존 .env를 덮어쓰지 않고 개발 자격 증명을 생성한다."""
from pathlib import Path
import os
import secrets
root = Path(__file__).resolve().parents[1]
path = root / '.env'
values = {
    'POSTGRES_DB': 'central', 'POSTGRES_USER': 'central',
    'POSTGRES_PASSWORD': secrets.token_urlsafe(32),
    'ADMIN_USERNAME': 'admin', 'ADMIN_PASSWORD': secrets.token_urlsafe(24),
    'IOT_DEVICE_ID': 'iot-001', 'IOT_DEVICE_TOKEN': secrets.token_urlsafe(32),
    'CENTRAL_PORT': '8200', 'COOKIE_SECURE': 'false',
}
try:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    raise SystemExit('기존 .env를 유지합니다.')
with os.fdopen(fd, 'w') as stream:
    stream.write(''.join(f'{key}={value}\n' for key, value in values.items()))
print('개발용 .env 생성 완료. 자격 증명은 해당 파일에서 확인하세요.')
