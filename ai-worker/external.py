"""Roboflow Universe 데이터셋을 내려받고 중앙 클래스로 자동 매핑한다.

다운로드·검증은 scripts/yolo_datasets.py 가 담당하며
같은 버전은 .artifacts/ai-assets/datasets/roboflow 에 받아 둔 것을 다시 사용한다.
"""
import json
from pathlib import Path
import re
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from ai_assets import CLASSES  # noqa: E402


def auto_mapping(names):
    """원본 클래스 이름을 CLASSES 번호로 바꾼다. 부정 클래스(NO-Hardhat 등)와 무관한 클래스는 None(버림).

    중앙신호등·스마트 콘은 자체 제작 장치라 외부 데이터셋에 없으므로 앞 4개 클래스로만 연결한다.
    """
    result = []
    for name in names:
        key = re.sub(r'[^a-z]', '', str(name).lower())
        if re.match(r'^(no|without|not)(hard|hat|helmet|safety|vest|mask)', key):
            result.append(None)
        elif key in {'person', 'people', 'worker', 'workers', 'human', 'pedestrian'}:
            result.append(0)
        elif 'vest' in key:
            result.append(1)
        elif 'cone' in key:
            result.append(2)
        elif re.fullmatch(r'(hard|safety|work|construction|white|yellow|blue|red|orange|green)?hat', key) or 'hardhat' in key or 'helmet' in key:
            result.append(3)
        else:
            result.append(None)
    return result


def track_progress(state):
    """roboflow SDK가 내려받기·압축 해제에 쓰는 tqdm을 감싸 진행률을 state에 기록한다."""
    from roboflow.core import version
    from roboflow.util import general

    class Progress:
        def __init__(self, iterable=None, total=None, **kwargs):
            self.iterable = iterable
            self.total = total or (len(iterable) if hasattr(iterable, '__len__') else None)
            # 첫 진행 막대는 zip 내려받기, 다음은 압축 해제다.
            state['phase'] = 'DOWNLOAD' if state['phase'] == 'EXPORT' else 'EXTRACT'
            state['progress'] = 0

        def __iter__(self):
            for index, item in enumerate(self.iterable):
                if self.total and index % 200 == 0:
                    state['progress'] = min(100, int(index * 100 / self.total))
                yield item
            state['progress'] = 100
            if state['phase'] == 'EXTRACT':
                state['phase'], state['progress'] = 'VALIDATE', None

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    version.tqdm = Progress
    general.tqdm = Progress


def process(job, api, env):
    stopped, lost = threading.Event(), threading.Event()
    lease = {'leaseToken': job['leaseToken']}
    state = {'phase': 'EXPORT', 'progress': None}

    def heartbeat():
        # 진행률을 자주 보여 주도록 3초마다 보고하며 임대도 함께 갱신한다.
        while not stopped.wait(3):
            try:
                api('/external/' + job['id'] + '/heartbeat', {**lease, 'phase': state['phase'], 'progress': state['progress']})
            except Exception:
                lost.set()
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        key = env.get('ROBOFLOW_API_KEY', '').strip()
        if not key:
            raise ValueError('중앙 .env에 ROBOFLOW_API_KEY가 없습니다.')
        from yolo_datasets import download_roboflow_universe
        track_progress(state)
        record, version = download_roboflow_universe(job['url'], key)
        mapping = auto_mapping(record['classes'])
        if not any(value is not None for value in mapping):
            raise ValueError('person·safety_vest·traffic_cone·hard_hat과 연결되는 클래스가 없습니다. 원본 클래스: '
                             + ', '.join(str(c) for c in record['classes'])[:200] + '. 다른 버전(/dataset/번호)을 지정해 보세요.')
        if lost.is_set():
            raise RuntimeError('lease lost')
        counts = {'train': record['train_images'], 'val': record['val_images'], 'test': record['test_images'],
                  'boxes': record['boxes']}
        api('/external/' + job['id'] + '/complete', {**lease, 'name': record['name'][:200], 'version': int(version),
            'yamlPath': str(record['yaml']), 'classes': [str(c)[:100] for c in record['classes']], 'mapping': mapping,
            'counts': json.dumps(counts)})
        print('외부 데이터셋 준비 완료:', job['id'], record['name'], flush=True)
    except Exception as exc:
        print('외부 데이터셋 실패:', job['id'], type(exc).__name__, flush=True)
        message = str(exc) if exc.__class__.__name__ in {'DatasetImportError', 'ValueError'} else type(exc).__name__
        if key := env.get('ROBOFLOW_API_KEY', ''):
            message = message.replace(key, '***')
        if not lost.is_set():
            try:
                api('/external/' + job['id'] + '/fail', {**lease, 'error': ('불러오기 실패: ' + message)[:500]})
            except Exception:
                pass
    finally:
        stopped.set()
        thread.join(timeout=35)
