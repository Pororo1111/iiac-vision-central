"""실제 PostgreSQL·인증 API로 학습 실행 생성과 접근 제어를 검증한다."""
import importlib.util
import argparse
import json
from pathlib import Path
from urllib import request,error
import sys

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('cli',ROOT/'scripts/train-test-video.py')
cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
BASE='http://127.0.0.1:8200'


def post(opener,path,body,csrf=True):
    headers={'Content-Type':'application/json'}
    if csrf:
        with opener.open(BASE+'/api/auth/csrf') as r:token=json.load(r)
        headers[token['headerName']]=token['token']
    req=request.Request(BASE+'/api'+path,json.dumps(body).encode(),headers,method='POST')
    with opener.open(req) as response:return json.load(response)


def expect(code,action):
    try: action()
    except error.HTTPError as exc:
        assert exc.code==code,(exc.code,exc.read())
    else:raise AssertionError('요청이 차단되지 않았습니다.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run',type=Path,required=True,help='collect와 analyze를 완료한 검증 실행 폴더')
    args=parser.parse_args()
    opener=cli.admin_session(BASE)
    source=json.loads((args.source_run/'run.json').read_text())
    body={'name':'검증 영상 자동 선별','sampleIds':source['sampleIds'],'confidence':0.6}
    expect(401,lambda: request.urlopen(BASE+'/api/training/runs'))
    expect(403,lambda:post(opener,'/training/runs',body,False))
    expect(409,lambda:post(opener,'/training/runs',{**body,'sampleIds':[source['sampleIds'][0]]*2}))
    expect(400,lambda:post(opener,'/training/runs',{**body,'confidence':0}))
    result=post(opener,'/training/runs',body)
    expect(409,lambda:post(opener,'/training/runs',body))
    expect(409,lambda:post(opener,'/training/runs/'+result['id']+'/train',{'epochs':1}))
    (ROOT/'.artifacts/verify-training-run.json').write_text(json.dumps({'id':result['id']}))
    print('실제 DB/API: 인증·CSRF·중복 샘플·입력 범위·동시 실행·단계 전이 검사 통과')
    print('생성 실행:',result['id'])
