"""자동 진행의 평가 통과 이후 분기(후보 등록·배포 요청·중단 사유)를 가짜 작은 파일로 격리 검증한다.

실제 학습은 하지 않는다. 평가 작업 완료 보고를 워커 API로 보내 afterEvaluate 경로만 확인한다.
실행 중인 학습 작업이 있으면 활성 작업 제약 때문에 건너뛴다.
"""
import hashlib
import json
from pathlib import Path
import shutil
import uuid
from urllib import request
from local_fixtures import DeviceFixture, sql
from dev import environment
from training_workflow import cli, BASE

ROOT = Path(__file__).resolve().parents[1]
TOKEN = environment()['AI_WORKER_TOKEN']


def complete(task, lease, summary):
    body = json.dumps({'leaseToken': lease, 'summary': json.dumps(summary)}).encode()
    req = request.Request(f'http://127.0.0.1:8201/internal/ai/training/tasks/{task}/complete', body,
                          {'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'})
    with request.urlopen(req) as response:
        assert json.load(response)['accepted']


def evaluated_run(automation, payload, eligible=True):
    """EVALUATING 상태 실행과 임대 중인 EVALUATE 작업을 만든 뒤 완료를 보고한다."""
    run, task, lease = (str(uuid.uuid4()) for _ in range(3))
    folder = ROOT / '.artifacts/training-runs' / run
    (folder / 'release').mkdir(parents=True)
    (folder / 'release/model.pt').write_bytes(payload)
    sql(f"""INSERT INTO training_runs(id,name,state,snapshot,automation) VALUES('{run}','자동 후속 검증','EVALUATING','[]','{json.dumps(automation)}'::jsonb);
      INSERT INTO training_tasks(id,run_id,action,status,parameters,lease_token,lease_until)
      VALUES('{task}','{run}','EVALUATE','RUNNING','{{}}','{lease}',now()+interval '120 seconds');""")
    complete(task, lease, {'modelSha256': hashlib.sha256(payload).hexdigest(),
                           'evaluation': {'eligibleForPromotion': eligible, 'deltaMap50_95': 0.0}})
    return run, json.loads(sql(f"SELECT summary->'automation' FROM training_runs WHERE id='{run}'"))


def main():
    if int(sql("SELECT count(*) FROM training_tasks WHERE status IN ('QUEUED','RUNNING')")):
        print('실행 중인 학습 작업이 있어 건너뜁니다.')
        return
    device = DeviceFixture()
    runs = []
    try:
        sql(f"UPDATE devices SET registered_at=now() WHERE device_id='{device.id}'")
        payload = b'automation-fixture-not-a-real-model'

        # 기본 정책(PINNED)인 장치에는 자동 배포하지 않고 후보 등록에서 멈춘다.
        run, outcome = evaluated_run({'epochs': 1, 'deviceId': device.id}, payload + b'-pinned')
        runs.append(run)
        assert outcome == {'release': 'REGISTERED', 'deployment': 'STOPPED', 'reason': 'DEVICE_PINNED'}, outcome
        sql(f"INSERT INTO device_model_policies(device_id,mode,updated_by) VALUES('{device.id}','AUTO','verify')")

        run, outcome = evaluated_run({'epochs': 1, 'deviceId': device.id}, payload)
        runs.append(run)
        assert outcome['release'] == 'REGISTERED' and outcome['deployment'] == 'REQUESTED', outcome
        status = sql(f"SELECT status||','||device_id FROM model_deployments WHERE id='{outcome['deploymentId']}'").strip()
        assert status == 'PENDING,' + device.id, status

        # 같은 장치에 진행 중 배포가 있으면 등록은 유지하고 배포 요청만 중단 사유를 남긴다.
        run, outcome = evaluated_run({'epochs': 1, 'deviceId': device.id}, payload + b'-2')
        runs.append(run)
        assert outcome == {'release': 'REGISTERED', 'deployment': 'STOPPED', 'reason': 'DEPLOYMENT_ACTIVE'}, outcome

        run, outcome = evaluated_run({'epochs': 1, 'deviceId': None}, payload + b'-3')
        runs.append(run)
        assert outcome == {'release': 'REGISTERED', 'deployment': 'SKIPPED_NO_DEVICE'}, outcome

        run, outcome = evaluated_run({'epochs': 1, 'deviceId': device.id}, payload + b'-4', eligible=False)
        runs.append(run)
        assert outcome == {'release': 'STOPPED', 'reason': 'EVALUATION_REJECTED'}, outcome
        assert sql(f"SELECT count(*) FROM model_releases WHERE id='{run}'").strip() == '0'

        # 수동 실행(automation 없음)은 평가 뒤 아무것도 자동으로 하지 않는다.
        manual, task, lease = (str(uuid.uuid4()) for _ in range(3))
        runs.append(manual)
        sql(f"""INSERT INTO training_runs(id,name,state,snapshot) VALUES('{manual}','수동 검증','EVALUATING','[]');
          INSERT INTO training_tasks(id,run_id,action,status,parameters,lease_token,lease_until)
          VALUES('{task}','{manual}','EVALUATE','RUNNING','{{}}','{lease}',now()+interval '120 seconds');""")
        complete(task, lease, {'evaluation': {'eligibleForPromotion': True}})
        assert sql(f"SELECT summary ? 'automation' FROM training_runs WHERE id='{manual}'").strip() == 'f'
        print('자동 후속 단계: 고정 장치 차단·등록·배포 요청·진행 중 배포 중단·장치 미지정·평가 미달·수동 실행 구분 검사 통과')
    finally:
        ids = ",".join(f"'{r}'" for r in runs) or "NULL"
        sql(f"""DELETE FROM device_model_policies WHERE device_id='{device.id}';
          DELETE FROM model_deployments WHERE device_id='{device.id}' OR model_id IN ({ids});
          DELETE FROM model_releases WHERE id IN ({ids});
          DELETE FROM training_tasks WHERE run_id IN ({ids});
          DELETE FROM training_runs WHERE id IN ({ids});""")
        device.cleanup()
        for r in runs:
            shutil.rmtree(ROOT / '.artifacts/training-runs' / r, ignore_errors=True)


if __name__ == '__main__':
    main()
