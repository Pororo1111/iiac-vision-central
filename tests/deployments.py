"""임시 장치·가짜 작은 파일로 배포 계약을 검증하며 현장 모델은 변경하지 않는다."""
import hashlib
import json
from pathlib import Path
import shutil
import uuid
from urllib import request,error
from local_fixtures import DeviceFixture,sql
from training_workflow import cli,BASE,post,expect


def iot(device,path,body=None):
    headers={'Authorization':'Bearer '+device.token}
    if body is not None:headers['Content-Type']='application/json'
    req=request.Request(BASE+'/api/iot'+path, None if body is None else json.dumps(body).encode(),headers)
    with request.urlopen(req) as response:
        data=response.read()
        if response.status==204:return None
        return data if '/model' in path else json.loads(data)


def main():
    first,second=DeviceFixture(),DeviceFixture()
    id=str(uuid.uuid4())
    folder=Path(__file__).resolve().parents[1]/'.artifacts/training-runs'/id
    payload=b'protocol-fixture-not-a-real-model'
    sha=hashlib.sha256(payload).hexdigest()
    try:
        sql(f"UPDATE devices SET registered_at=now() WHERE device_id IN ('{first.id}','{second.id}');")
        sql(f"INSERT INTO training_runs(id,name,state,snapshot,summary) VALUES('{id}','검증 배포','TRAINED','[]','{{}}');")
        (folder/'release').mkdir(parents=True)
        (folder/'release/model.pt').write_bytes(payload)
        admin=cli.admin_session(BASE)
        # 새 장치 기본 정책은 PINNED(현재 모델 유지)라 배포 계약 검사는 AUTO 정책 장치로 한다.
        post(admin,f'/training/device-policies/{first.id}',{'mode':'AUTO'})
        expect(409,lambda:post(admin,f'/training/runs/{id}/release',{}))
        summary=json.dumps({'modelSha256':sha,'evaluation':{'eligibleForPromotion':True}})
        sql(f"UPDATE training_runs SET state='EVALUATED',summary='{summary}'::jsonb WHERE id='{id}';")
        release=post(admin,f'/training/runs/{id}/release',{})
        assert release['sha256']==sha
        expect(401,lambda:request.urlopen(BASE+'/api/iot/deployments/desired'))
        expect(403,lambda:post(admin,'/training/deployments',{'modelId':id,'deviceId':first.id},False))
        assert iot(first,'/deployments/desired') is None
        beat=lambda device:iot(device,'/heartbeat',{'deviceId':device.id,'modelName':'','runtimeStatus':'STOPPED'})['deployment']
        assert beat(first) is None
        deploy=post(admin,'/training/deployments',{'modelId':id,'deviceId':first.id})
        expect(409,lambda:post(admin,'/training/deployments',{'modelId':id,'deviceId':first.id}))
        desired=iot(first,'/deployments/desired')
        assert desired['id']==deploy['id'] and desired['sizeBytes']==len(payload)
        assert desired['modelId']==id and desired['modelName']=='검증 배포'
        assert beat(first)==desired and beat(second) is None
        path='/deployments/'+deploy['id']
        expect(404,lambda:iot(second,path+'/model'))
        expect(404,lambda:iot(second,path+'/status',{'status':'DOWNLOADING'}))
        expect(409,lambda:iot(first,path+'/status',{'status':'APPLIED','activeSha256':sha}))
        assert iot(first,path+'/model')==payload
        for status in ['DOWNLOADING','VALIDATING','APPLYING']:
            assert iot(first,path+'/status',{'status':status})['accepted']
            assert iot(first,path+'/status',{'status':status})['accepted']
        expect(409,lambda:iot(first,path+'/status',{'status':'APPLIED','activeSha256':'0'*64}))
        assert iot(first,path+'/status',{'status':'APPLIED','activeSha256':sha})['accepted']
        assert iot(first,path+'/status',{'status':'APPLIED','activeSha256':sha})['accepted']
        assert iot(first,'/deployments/desired') is None and beat(first) is None
        expect(409,lambda:iot(first,path+'/model'))
        expect(409,lambda:iot(first,path+'/status',{'status':'FAILED'}))

        # 각 활성 단계의 실패와 적용 중 롤백은 종료 상태로 유지되어야 한다.
        previous_sha='1'*64
        for terminal,steps in [
            ('FAILED',[]),
            ('FAILED',['DOWNLOADING']),
            ('FAILED',['DOWNLOADING','VALIDATING']),
            ('FAILED',['DOWNLOADING','VALIDATING','APPLYING']),
            ('ROLLED_BACK',['DOWNLOADING','VALIDATING','APPLYING']),
        ]:
            deployment=post(admin,'/training/deployments',{'modelId':id,'deviceId':first.id})
            current_path='/deployments/'+deployment['id']
            expect(409,lambda:iot(first,current_path+'/status',{'status':'ROLLED_BACK'}))
            for status in steps:
                assert iot(first,current_path+'/status',{'status':status,'activeSha256':previous_sha})['accepted']
            report={'status':terminal,'activeSha256':previous_sha,'message':'격리된 실패·복구 계약 검증'}
            assert iot(first,current_path+'/status',report)['accepted']
            assert iot(first,current_path+'/status',report)['accepted']
            expect(409,lambda:iot(first,current_path+'/status',{**report,'activeSha256':'2'*64}))
            expect(409,lambda:iot(first,current_path+'/status',{'status':'DOWNLOADING'}))
            expect(409,lambda:iot(first,current_path+'/model'))
            assert iot(first,'/deployments/desired') is None
            stored=json.loads(sql(f"SELECT row_to_json(d) FROM model_deployments d WHERE id='{deployment['id']}';"))
            assert stored['status']==terminal and stored['active_sha256']==previous_sha
        # 적용 전 단계(PENDING·DOWNLOADING·VALIDATING)는 관리자가 취소할 수 있고, 취소 후 IoT 보고·다운로드는 막힌다.
        for steps in [[],['DOWNLOADING'],['DOWNLOADING','VALIDATING']]:
            deployment=post(admin,'/training/deployments',{'modelId':id,'deviceId':first.id})
            current_path='/deployments/'+deployment['id']
            expect(403,lambda:post(admin,f"/training/deployments/{deployment['id']}/cancel",{},False))
            for status in steps:
                assert iot(first,current_path+'/status',{'status':status})['accepted']
            canceled=post(admin,f"/training/deployments/{deployment['id']}/cancel",{})
            assert canceled['status']=='CANCELED' and beat(first) is None
            assert post(admin,f"/training/deployments/{deployment['id']}/cancel",{})['status']=='CANCELED'
            assert iot(first,'/deployments/desired') is None
            expect(409,lambda:iot(first,current_path+'/model'))
            expect(409,lambda:iot(first,current_path+'/status',{'status':'FAILED'}))
        deployment=post(admin,'/training/deployments',{'modelId':id,'deviceId':first.id})
        current_path='/deployments/'+deployment['id']
        for status in ['DOWNLOADING','VALIDATING','APPLYING']:
            assert iot(first,current_path+'/status',{'status':status})['accepted']
        expect(409,lambda:post(admin,f"/training/deployments/{deployment['id']}/cancel",{}))
        assert iot(first,current_path+'/status',{'status':'FAILED'})['accepted']
        expect(409,lambda:post(admin,f"/training/deployments/{deployment['id']}/cancel",{}))
        expect(404,lambda:post(admin,f"/training/deployments/{uuid.uuid4()}/cancel",{}))
        (folder/'release/model.pt').write_bytes(b'changed')
        expect(409,lambda:post(admin,'/training/deployments',{'modelId':id,'deviceId':first.id}))
        print('배포 계약: 인증·CSRF·평가 제한·파일 해시·장치 격리·중복 배포·순차 전이·재시도·활성 해시·실패·롤백·취소·heartbeat 전달 검사 통과')
    finally:
        sql(f"DELETE FROM device_model_policies WHERE device_id IN ('{first.id}','{second.id}');DELETE FROM model_deployments WHERE model_id='{id}';DELETE FROM model_releases WHERE id='{id}';DELETE FROM training_runs WHERE id='{id}';")
        first.cleanup();second.cleanup()
        if folder.exists():shutil.rmtree(folder)


if __name__=='__main__':main()
