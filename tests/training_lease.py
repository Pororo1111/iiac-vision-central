"""만료한 학습 임대의 결과를 실제 PostgreSQL과 내부 API로 차단한다."""
import json
import uuid
from urllib import request
from local_fixtures import sql
from training_workflow import cli,BASE,expect
from dev import environment


def main():
    run,task,lease=(str(uuid.uuid4()) for _ in range(3))
    try:
        sql(f"""INSERT INTO training_runs(id,name,state,snapshot) VALUES('{run}','검증 임대','TRAINING','[]');
        INSERT INTO training_tasks(id,run_id,action,status,parameters,lease_token,lease_until)
        VALUES('{task}','{run}','TRAIN','RUNNING','{{}}','{lease}',now()-interval '1 second');""")
        req=request.Request('http://127.0.0.1:8201/internal/ai/training/tasks/'+task+'/complete',
            json.dumps({'leaseToken':lease,'summary':'{}'}).encode(),
            {'Authorization':'Bearer '+environment()['AI_WORKER_TOKEN'],'Content-Type':'application/json'})
        expect(409,lambda:request.urlopen(req))
        with cli.admin_session(BASE).open(BASE+'/api/training/runs') as response:
            runs=json.load(response)
        item=next(value for value in runs if value['id']==run)
        assert item['state']=='FAILED' and 'snapshot' not in item
        assert sql(f"SELECT status FROM training_tasks WHERE id='{task}'").strip()=='FAILED'
        print('만료 임대 결과 차단·실패 상태 기록·목록 스냅샷 제외 검증 통과')
    finally:
        sql(f"DELETE FROM training_tasks WHERE run_id='{run}';DELETE FROM training_runs WHERE id='{run}';")


if __name__=='__main__':main()
