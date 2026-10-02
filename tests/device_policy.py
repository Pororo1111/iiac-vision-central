"""장치별 배포 정책(고정·자동)과 현황판을 임시 장치·가짜 작은 파일로 검증한다. 현장 모델은 변경하지 않는다."""
import hashlib
import json
from pathlib import Path
import shutil
import uuid
from urllib import request
from local_fixtures import DeviceFixture, sql
from training_workflow import cli, BASE, post, expect

ROOT = Path(__file__).resolve().parents[1]


def get(opener, path):
    with opener.open(BASE + '/api' + path) as response:
        return json.load(response)


def iot(device, path, body):
    req = request.Request(BASE + '/api/iot' + path, json.dumps(body).encode(),
                          {'Authorization': 'Bearer ' + device.token, 'Content-Type': 'application/json'})
    with request.urlopen(req) as response:
        return json.load(response)


def release(payload):
    """평가를 통과해 등록된 후보를 파일과 함께 직접 만든다."""
    id = str(uuid.uuid4())
    folder = ROOT / '.artifacts/training-runs' / id / 'release'
    folder.mkdir(parents=True)
    (folder / 'model.pt').write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()
    summary = json.dumps({'modelSha256': sha, 'evaluation': {'eligibleForPromotion': True,
                          'models': {'candidate': {'mAP50_95': 0.51}, 'baseline': {'mAP50_95': 0.5}}}})
    sql(f"""INSERT INTO training_runs(id,name,state,snapshot,summary) VALUES('{id}','정책 검증','EVALUATED','[]','{summary}'::jsonb);
      INSERT INTO model_releases(id,sha256,size_bytes) VALUES('{id}','{sha}',{len(payload)});""")
    return id, sha


def main():
    device = DeviceFixture()
    models = []
    try:
        sql(f"UPDATE devices SET registered_at=now() WHERE device_id='{device.id}'")
        admin = cli.admin_session(BASE)
        (a, sha_a), (b, sha_b) = release(b'policy-fixture-a'), release(b'policy-fixture-b')
        models += [a, b]
        beat = lambda sha=None: iot(device, '/heartbeat', {'deviceId': device.id, 'modelName': 'm', 'runtimeStatus': 'STOPPED',
                                                           **({'activeSha256': sha} if sha else {})})
        row = lambda: next(r for r in get(admin, '/training/device-policies') if r['policy']['deviceId'] == device.id)
        status = lambda id: sql(f"SELECT status FROM model_deployments WHERE id='{id}'").strip()
        policy = lambda body: post(admin, f'/training/device-policies/{device.id}', body)

        # 새 장치는 PINNED(현재 모델 유지)이며 관리자 배포도 막힌다.
        assert row()['policy']['mode'] == 'PINNED' and row()['policy']['pinnedModelId'] is None
        expect(409, lambda: post(admin, '/training/deployments', {'modelId': a, 'deviceId': device.id}))
        catalog = {m['id']: m for m in get(admin, '/training/models')}
        assert catalog[a]['candidateMap'] == 0.51 and catalog[a]['baselineMap'] == 0.5

        # heartbeat 활성 해시는 저장되고, 생략하면 유지되며, 형식이 틀리면 400이다.
        beat('0' * 64); beat()
        assert row()['policy']['activeSha256'] == '0' * 64
        expect(400, lambda: beat('XYZ'))

        # 입력 검증: CSRF 없음 403, 모드 오류 400, 없는 모델 409, 없는 장치 404.
        expect(403, lambda: post(admin, f'/training/device-policies/{device.id}', {'mode': 'AUTO'}, False))
        expect(400, lambda: policy({'mode': 'FREE'}))
        expect(409, lambda: policy({'mode': 'PINNED', 'modelId': str(uuid.uuid4())}))
        expect(404, lambda: post(admin, '/training/device-policies/verify-none-device', {'mode': 'AUTO'}))

        # A로 고정하면 배포가 요청된다. 다른 모델 B의 관리자 배포는 막힌다.
        first = policy({'mode': 'PINNED', 'modelId': a, 'note': '검증 고정'})
        assert first['deployment'] == 'REQUESTED' and status(first['deploymentId']) == 'PENDING'
        assert beat()['deployment']['id'] == first['deploymentId']
        expect(409, lambda: post(admin, '/training/deployments', {'modelId': b, 'deviceId': device.id}))

        # B로 고정을 바꾸면 적용 전 A 배포는 취소되고 B가 요청된다. 같은 고정을 다시 보내면 진행 중 배포를 유지한다.
        second = policy({'mode': 'PINNED', 'modelId': b})
        assert status(first['deploymentId']) == 'CANCELED' and second['deployment'] == 'REQUESTED'
        again = policy({'mode': 'PINNED', 'modelId': b})
        assert again == {**second, 'deployment': 'IN_PROGRESS'}

        # 적용 중에는 다른 모델로 고정을 바꿀 수 없고 정책도 그대로다.
        for step in ['DOWNLOADING', 'VALIDATING', 'APPLYING']:
            iot(device, f"/deployments/{second['deploymentId']}/status", {'status': step})
        expect(409, lambda: policy({'mode': 'PINNED', 'modelId': a}))
        assert row()['policy']['pinnedModelId'] == b
        iot(device, f"/deployments/{second['deploymentId']}/status", {'status': 'APPLIED', 'activeSha256': sha_b})

        # 이미 실행 중인 모델로 고정하면 배포를 만들지 않는다. 활성 해시가 다르면 drift로 표시하고 고정 모델 재배포는 허용한다.
        beat(sha_b)
        assert policy({'mode': 'PINNED', 'modelId': b})['deployment'] == 'ALREADY_ACTIVE' and row()['drift'] is False
        beat('1' * 64)
        assert row()['drift'] is True
        redeploy = post(admin, '/training/deployments', {'modelId': b, 'deviceId': device.id})
        assert row()['latestDeployment']['id'] == redeploy['id']

        # 모델 없는 고정(현재 모델 유지)은 적용 전 배포를 취소하고 새 배포를 만들지 않는다.
        hold = policy({'mode': 'PINNED'})
        assert hold['deployment'] == 'NONE' and status(redeploy['id']) == 'CANCELED' and beat()['deployment'] is None

        # AUTO는 고정 모델을 비우고 관리자·자동 배포를 모두 허용한다.
        auto = policy({'mode': 'AUTO', 'modelId': a})
        assert auto['pinnedModelId'] is None and row()['policy']['mode'] == 'AUTO'
        manual = post(admin, '/training/deployments', {'modelId': a, 'deviceId': device.id})
        assert status(manual['id']) == 'PENDING'

        history = get(admin, f'/training/device-policies/{device.id}/history')
        assert [h['mode'] for h in history][:2] == ['AUTO', 'PINNED'] and history[-1]['note'] == '검증 고정'
        print('배포 정책: 기본 고정·관리자/자동 차단·고정 변경 시 취소·재요청·적용 중 거부·이미 적용·drift·현재 모델 유지·자동 전환·이력 검사 통과')
    finally:
        ids = ",".join(f"'{m}'" for m in models) or "NULL"
        sql(f"""DELETE FROM device_model_policies WHERE device_id='{device.id}';
          DELETE FROM model_deployments WHERE device_id='{device.id}';
          DELETE FROM model_releases WHERE id IN ({ids});
          DELETE FROM training_runs WHERE id IN ({ids});""")
        device.cleanup()
        for m in models:
            shutil.rmtree(ROOT / '.artifacts/training-runs' / m, ignore_errors=True)


if __name__ == '__main__':
    main()
