"""실제 PostgreSQL·워커로 자동 진행 실행이 구성→학습→평가→후보 등록→배포 요청까지 이어지는지 검증한다.

collect·analyze를 마친 검증 실행 폴더의 이미지를 사용한다. 학습은 --epochs(기본 1)로 짧게 수행하며
성능 향상을 검증하지 않는다. 평가 기준 미달이면 후보 등록에서 멈추는 것이 정상 결과다.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import time
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cli', ROOT / 'scripts/train-test-video.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
BASE = 'http://127.0.0.1:8200'


def call(opener, path, body=None):
    headers = {'Content-Type': 'application/json'}
    if body is not None:
        with opener.open(BASE + '/api/auth/csrf') as r:
            token = json.load(r)
        headers[token['headerName']] = token['token']
    req = request.Request(BASE + '/api' + path, None if body is None else json.dumps(body).encode(), headers,
                          method='GET' if body is None else 'POST')
    with opener.open(req) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True)
    parser.add_argument('--device-id', required=True, help='배포 요청을 받을 검증 전용 장치 ID')
    parser.add_argument('--epochs', type=int, default=1)
    parser.add_argument('--timeout', type=int, default=3600)
    args = parser.parse_args()
    opener = cli.admin_session(BASE)
    source = json.loads((args.source_run / 'run.json').read_text())
    body = {'name': '자동 진행 검증', 'sampleIds': source['sampleIds'], 'confidence': 0.6,
            'auto': {'epochs': args.epochs, 'deviceId': args.device_id}}
    for bad in ({'epochs': 0, 'deviceId': None}, {'epochs': 1, 'deviceId': 'bad id'}):
        try:
            call(opener, '/training/runs', {**body, 'auto': bad})
        except error.HTTPError as exc:
            assert exc.code == 400, exc.code
        else:
            raise AssertionError('잘못된 자동 진행 입력이 허용됐습니다.')
    run = call(opener, '/training/runs', body)
    print('생성 실행:', run['id'])
    seen, deadline = [run['state']], time.time() + args.timeout
    while time.time() < deadline:
        run = call(opener, '/training/runs/' + run['id'])
        if run['state'] != seen[-1]:
            seen.append(run['state'])
            print('상태:', run['state'], flush=True)
        summary = json.loads(run['summary'])
        if run['state'] == 'FAILED' or 'automation' in summary:
            break
        time.sleep(5)
    else:
        raise AssertionError('시간 안에 자동 진행이 끝나지 않았습니다: ' + ' → '.join(seen))
    assert run['state'] != 'FAILED', summary.get('error')
    for state in ('TRAINING', 'EVALUATING', 'EVALUATED'):
        assert state in seen or state == 'TRAINING', seen
    outcome, evaluation = summary['automation'], summary['evaluation']
    if evaluation['eligibleForPromotion']:
        assert outcome == {**outcome, 'release': 'REGISTERED', 'deployment': 'REQUESTED'}, outcome
        deployment = next(d for d in call(opener, '/training/deployments') if d['id'] == outcome['deploymentId'])
        assert deployment['deviceId'] == args.device_id and deployment['status'] == 'PENDING', deployment
    else:
        assert outcome == {'release': 'STOPPED', 'reason': 'EVALUATION_REJECTED'}, outcome
        assert all(m['id'] != run['id'] for m in call(opener, '/training/models'))
    (ROOT / '.artifacts/verify-automation-run.json').write_text(json.dumps({'id': run['id'], 'states': seen,
                                                                           'automation': outcome}))
    print('상태 전이:', ' → '.join(seen))
    print('평가 mAP50-95 변화:', round(evaluation['deltaMap50_95'], 4), '통과' if evaluation['eligibleForPromotion'] else '미달')
    print('자동 후속 결과:', outcome)


if __name__ == '__main__':
    main()
