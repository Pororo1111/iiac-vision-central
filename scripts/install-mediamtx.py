"""공식 릴리스와 체크섬을 확인해 로컬 개발용 MediaMTX 바이너리를 설치한다."""
import hashlib
import io
from pathlib import Path
import platform
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.21.1"
ARCH = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine())
if not ARCH:
    raise SystemExit("지원하지 않는 CPU 아키텍처입니다: " + platform.machine())
NAME = f"mediamtx_v{VERSION}_darwin_{ARCH}.tar.gz"
BASE = f"https://github.com/bluenviron/mediamtx/releases/download/v{VERSION}/"
target = ROOT / ".artifacts/tools/mediamtx"

with urllib.request.urlopen(BASE + "checksums.sha256", timeout=30) as response:
    checksums = response.read().decode().splitlines()
expected = next((line.split()[0] for line in checksums if line.split()[-1].lstrip("*") == NAME), None)
if not expected:
    raise SystemExit("공식 체크섬에서 설치 파일을 찾지 못했습니다")
with urllib.request.urlopen(BASE + NAME, timeout=120) as response:
    archive = response.read()
if hashlib.sha256(archive).hexdigest() != expected:
    raise SystemExit("MediaMTX 다운로드 체크섬이 일치하지 않습니다")
with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
    member = bundle.getmember("mediamtx")
    source = bundle.extractfile(member)
    if source is None:
        raise SystemExit("MediaMTX 바이너리가 없습니다")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    temp.write_bytes(source.read())
    temp.chmod(0o755)
    temp.replace(target)
print(f"MediaMTX v{VERSION} 설치 완료: {target}")
