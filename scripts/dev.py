"""Docker 없이 로컬 PostgreSQL, Spring Boot, Next.js, AI 워커를 실행한다."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / '.artifacts/local-dev'
STATE = DATA / 'processes.json'
PG = Path('/opt/homebrew/opt/postgresql@17/bin')
NODE = Path('/opt/homebrew/opt/node@22/bin')
JAVA = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home')


def environment():
    env = dict(os.environ)
    for line in (ROOT / '.env').read_text().splitlines():
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            env[key] = value
    media_host = env.get('MEDIA_HOST') or '127.0.0.1'
    origins = ['http://localhost:8200', 'http://127.0.0.1:8200'] + ([env['PUBLIC_ORIGIN']] if env.get('PUBLIC_ORIGIN') else [])
    env.update(JAVA_HOME=str(JAVA), PATH=str(NODE) + ':' + str(JAVA / 'bin') + ':' + env.get('PATH', ''),
        DATABASE_URL='jdbc:postgresql://127.0.0.1:55432/' + env.get('POSTGRES_DB', 'central'),
        PGHOST='127.0.0.1', PGPORT='55432', PGUSER=env.get('POSTGRES_USER', 'central'),
        PGPASSWORD=env['POSTGRES_PASSWORD'], PGDATABASE=env.get('POSTGRES_DB', 'central'),
        COOKIE_SECURE='false', MEDIA_ROOT=str(DATA / 'media'), TRAINING_RUNS_ROOT=str(ROOT / '.artifacts/training-runs'),
        BASE_MODEL_PATH=str(ROOT / '.artifacts/ai-assets/models/ppe4-rf27-local-m-20260914.pt'),
        MEDIA_PUBLIC_URL=env.get('MEDIA_PUBLIC_URL') or 'http://127.0.0.1:8889',
        # 사설망 주소는 .env에서 받아 media/mediamtx.yml의 loopback 기본값을 덮어쓴다.
        MTX_RTMPADDRESS=media_host + ':1935', MTX_WEBRTCLOCALUDPADDRESS=media_host + ':8189',
        MTX_WEBRTCLOCALTCPADDRESS=media_host + ':8189', MTX_WEBRTCADDITIONALHOSTS=media_host,
        MTX_WEBRTCALLOWORIGINS=','.join(origins),
        DEV_API_ORIGIN='http://127.0.0.1:8201', NEXT_TELEMETRY_DISABLED='1')
    return env


def alive(pid):
    command = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True).stdout
    # 다른 프로그램에 재사용된 PID에는 종료 신호를 보내지 않는다.
    return str(ROOT) in command


def wait_http(url, seconds=180):
    for _ in range(seconds):
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError('서비스 시작 시간 초과. .artifacts/local-dev 로그를 확인하세요.')


def start(env):
    for binary in (PG / 'pg_ctl', NODE / 'node', JAVA / 'bin/java'):
        if not binary.exists():
            raise SystemExit('필요한 로컬 실행 파일이 없습니다: ' + str(binary))
    pgdata = DATA / 'postgres'
    if not (pgdata / 'PG_VERSION').exists():
        password_file = DATA / 'init-password'
        password_file.write_text(env['POSTGRES_PASSWORD']); password_file.chmod(0o600)
        try:
            subprocess.run([str(PG / 'initdb'), '-D', str(pgdata), '-U', env['PGUSER'], '--auth=scram-sha-256',
                '--encoding=UTF8', '--locale=C', '--pwfile=' + str(password_file)], check=True, stdout=subprocess.DEVNULL)
        finally:
            password_file.unlink(missing_ok=True)
    if subprocess.run([str(PG / 'pg_ctl'), '-D', str(pgdata), 'status'], stdout=subprocess.DEVNULL).returncode:
        subprocess.run([str(PG / 'pg_ctl'), '-D', str(pgdata), '-l', str(DATA / 'postgres.log'),
            '-o', '-h 127.0.0.1 -p 55432 -k ' + str(DATA), 'start'], check=True, stdout=subprocess.DEVNULL)
    db = env['PGDATABASE']
    if not db.replace('_', '').isalnum():
        raise SystemExit('로컬 DB 이름은 영문·숫자·밑줄만 사용하세요.')
    check = subprocess.check_output([str(PG / 'psql'), '-d', 'postgres', '-Atc', f"SELECT 1 FROM pg_database WHERE datname='{db}'"], env=env, text=True)
    if not check.strip():
        subprocess.run([str(PG / 'createdb'), db], env=env, check=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    def launch(name, command, cwd):
        if name in state and alive(state[name]):
            return
        with (DATA / (name + '.log')).open('ab') as log:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=log, start_new_session=True)
        state[name] = process.pid
        STATE.write_text(json.dumps(state))
    gradle = shutil.which('gradle') or '/opt/homebrew/bin/gradle'
    mediamtx = ROOT / '.artifacts/tools/mediamtx'
    if not mediamtx.exists():
        raise SystemExit('python3 scripts/install-mediamtx.py 를 먼저 실행하세요.')
    launch('media', [str(mediamtx), str(ROOT / 'media/mediamtx.yml')], ROOT)
    wait_http('http://127.0.0.1:9997/v3/config/global/get', 30)
    launch('backend', [gradle, '-p', str(ROOT / 'backend'), '--no-daemon', 'bootRun', '--args=--server.port=8201 --server.address=127.0.0.1'], ROOT)
    wait_http('http://127.0.0.1:8201/api/health')
    launch('frontend', [str(NODE / 'node'), str(ROOT / 'frontend/node_modules/next/dist/bin/next'), 'dev', str(ROOT / 'frontend'), '--hostname', '127.0.0.1', '--port', '8200'], ROOT / 'frontend')
    wait_http('http://127.0.0.1:8200/login')
    python = os.environ.get('AI_PYTHON', str(ROOT / '.venv/bin/python'))
    if Path(python).exists():
        launch('worker', [python, '-u', str(ROOT / 'ai-worker/worker.py')], ROOT)
        # 영상 구간 분석·질문 해석은 학습 작업에 막히지 않게 별도 프로세스로 둔다.
        launch('observer', [python, '-u', str(ROOT / 'ai-worker/observer.py')], ROOT)
    print('로컬 개발 서버: http://localhost:8200 / AI 학습: http://localhost:8200/ai-training')


def stop():
    if STATE.exists():
        for name, pid in json.loads(STATE.read_text()).items():
            if alive(pid):
                os.killpg(pid, signal.SIGTERM)
                print(name, '종료 요청')
        STATE.unlink()
    if (DATA / 'postgres/PG_VERSION').exists():
        subprocess.run([str(PG / 'pg_ctl'), '-D', str(DATA / 'postgres'), 'stop', '-m', 'fast'], check=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'stop', 'status'])
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    if args.command == 'start': start(environment())
    elif args.command == 'stop': stop()
    else:
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
        for name, pid in state.items(): print(name, '실행 중' if alive(pid) else '중지', pid)
