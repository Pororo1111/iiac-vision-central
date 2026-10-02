"""중앙 작업 큐의 데이터셋 구성·학습·평가를 호스트 Python에서 수행한다."""
import hashlib
import importlib.util
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import threading
import uuid
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('training_cli', ROOT / 'scripts/train-test-video.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def execute(task, api, token):
    run = cli.RUNS / str(uuid.UUID(task['runId']))
    parameters = json.loads(task['parameters'])
    action = task['action']
    device = os.environ.get('TRAINING_DEVICE', 'mps')
    if action == 'PREPARE':
        record = api('/training/runs/' + task['runId'])
        samples = json.loads(record['snapshot'])
        (run / 'central-images').mkdir(parents=True, exist_ok=True)
        gemma = {'mode': 'AUTO_LABEL', 'items': {}}
        for sample in samples:
            sample_id = str(uuid.UUID(sample['id']))
            req = request.Request('http://127.0.0.1:8201/internal/ai/samples/' + sample_id + '/image',
                                  headers={'Authorization': 'Bearer ' + token})
            with request.urlopen(req, timeout=30) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != sample['sha256']:
                raise ValueError('수집 이미지 해시 불일치')
            (run / 'central-images' / (sample_id + '.jpg')).write_bytes(data)
            gemma['items'][sample_id] = json.loads(sample['proposal'])
        cli.save(run / 'gemma.json', gemma)
        external = json.loads(record.get('external') or '[]')
        groups = {}
        for sample in samples:
            # 같은 영상 가져오기, 같은 장치·카메라의 10분 구간을 한 묶음으로 본다. 직접 올린 이미지는 한 장씩.
            if sample.get('importId'):
                groups[sample['id']] = 'import:' + sample['importId']
            elif sample.get('deviceId'):
                groups[sample['id']] = 'device:%s/%s/%s' % (sample['deviceId'], sample.get('cameraId'), (sample.get('capturedAt') or sample['createdAt'])[:15])
            else:
                groups[sample['id']] = 'sample:' + sample['id']
        cli.save(run / 'run.json', {'sourceKind': 'CENTRAL_SNAPSHOT', 'sampleIds': [s['id'] for s in samples],
                                   'sourceSha256': hashlib.sha256(record['snapshot'].encode()).hexdigest(),
                                   'classes': cli.CLASSES, 'baseModel': record.get('baseModel') or 'default',
                                   'external': external, 'groups': groups})
        if samples:
            cli.run_with_progress(run, 'label', lambda: cli.label(run, parameters['confidence'], device))
        else:
            # 외부 데이터셋만으로 구성하는 실행. 자동 선별할 수집 이미지가 없다.
            cli.save(run / 'selection.json', {'policy': 'yolo-gemma-consensus-v1', 'threshold': parameters['confidence'], 'items': [], 'humanReviewed': False,
                                              'modelSha256': hashlib.sha256(cli.BASE_MODEL.read_bytes()).hexdigest()})
        selection = json.loads((run / 'selection.json').read_text())
        included = sum(item['include'] for item in selection['items'])
        if included == 0 and not external:
            raise ValueError('자동 선별을 통과한 이미지가 없습니다. 다른 이미지를 선택하거나 로보플로우 데이터셋을 포함하세요.')
        if external:
            cli.run_with_progress(run, 'external-label', lambda: cli.label_external(run, device, parameters['confidence']))
        cli.run_with_progress(run, 'dataset', lambda: cli.prepare_dataset(run, None, device))
        provenance = json.loads((run / 'combined-dataset/provenance.json').read_text())
        return {'selected': included, 'excluded': len(samples) - included,
                'external': provenance['counts'].get('external', {}),
                'externalLabel': json.loads((run / 'external-labels.json').read_text()) if external else {}, 'site': provenance['counts'].get('site', {}),
                'inherited': provenance['counts'].get('inherited', {}), 'inheritedFrom': provenance['counts'].get('inheritedFrom'),
                'baseModel': record.get('baseModel') or 'default',
                'policy': selection['policy'], 'humanReviewed': False,
                'selection': [{'sampleId': item['sampleId'], 'include': item['include'], 'reason': item['reason']}
                              for item in selection['items']]}
    if action == 'TRAIN':
        cli.run_with_progress(run, 'train', lambda: cli.train(run, parameters['epochs'], device, None, 0.0001))
        model = json.loads((run / 'model.json').read_text())
        return {'modelSha256': model['sha256'], 'epochs': parameters['epochs'], 'modelStatus': 'CANDIDATE'}
    cli.run_with_progress(run, 'evaluate', lambda: cli.evaluate(run, device))
    evaluation = json.loads((run / 'evaluation.json').read_text())
    if evaluation['eligibleForPromotion']:
        model = json.loads((run / 'model.json').read_text())
        source = Path(model['weights'])
        if hashlib.sha256(source.read_bytes()).hexdigest() != model['sha256']:
            raise ValueError('후보 모델 해시가 변경됐습니다.')
        release = run / 'release'
        release.mkdir(exist_ok=True)
        shutil.copy2(source, release / 'model.pt')
    return {'evaluation': evaluation}


def process(task, api, token):
    """학습은 자식 프로세스로 실행하여 임대 상실 때 중단할 수 있게 한다."""
    run = cli.RUNS / str(uuid.UUID(task['runId']))
    run.mkdir(parents=True, exist_ok=True)
    job_file = run / (str(uuid.UUID(task['id'])) + '.task.json')
    result_file = job_file.with_suffix('.result.json')
    cli.save(job_file, task)
    stopped, lost = threading.Event(), threading.Event()
    lease = {'leaseToken': task['leaseToken']}
    path = '/training/tasks/' + task['id']
    child = None

    def heartbeat():
        while not stopped.wait(20):
            try:
                api(path + '/heartbeat', lease)
            except Exception:
                lost.set()
                if child is not None and child.poll() is None:
                    child.terminate()
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        with (run / 'worker.log').open('ab') as log:
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(job_file)],
                                     stdout=log, stderr=log)
            while child.poll() is None:
                if lost.wait(1):
                    child.terminate()
                    try: child.wait(timeout=10)
                    except subprocess.TimeoutExpired: child.kill(); child.wait()
                    raise RuntimeError('작업 임대 상실')
            if child.returncode:
                detail = json.loads(result_file.read_text()).get('error') if result_file.exists() else None
                raise RuntimeError(detail or '학습 작업 실패. 로컬 worker.log를 확인하세요.')
        if lost.is_set():
            raise RuntimeError('작업 임대 상실')
        api(path + '/complete', {**lease, 'summary': result_file.read_text()})
    except Exception as exc:
        if not lost.is_set():
            api(path + '/fail', {**lease, 'error': str(exc)[:500]})
    finally:
        stopped.set()
        thread.join(timeout=35)
        if child is not None and child.poll() is None:
            child.terminate()
        job_file.unlink(missing_ok=True)


if __name__ == '__main__':
    from worker import api, TOKEN
    path = Path(sys.argv[1])
    try:
        cli.save(path.with_suffix('.result.json'), execute(json.loads(path.read_text()), api, TOKEN))
    except Exception as exc:
        # 외부 라이브러리 오류의 전체 경로나 상세값은 로컬 로그에만 남긴다.
        import traceback
        traceback.print_exc()
        message = str(exc) if isinstance(exc, ValueError) else '학습 처리 실패. 로컬 작업 로그를 확인하세요.'
        cli.save(path.with_suffix('.result.json'), {'error': message[:500]})
        sys.exit(1)
