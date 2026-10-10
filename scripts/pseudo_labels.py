"""기존 YOLO 박스와 Gemma 태그가 일치하는 이미지의 의사 라벨을 만든다."""
import hashlib
import math
from pathlib import Path

from ai_assets import CLASSES, compatible
POLICY = 'yolo-gemma-consensus-v1'


def _inside(coords):
    x, y, width, height = coords
    return (0 < width <= 1 and 0 < height <= 1 and width/2 <= x <= 1-width/2+1e-6
            and height/2 <= y <= 1-height/2+1e-6)


def fill_missing(detections, labeled, threshold=0.6):
    """부분 라벨 외부 데이터셋에서 원본에 없는 클래스를 기존 모델 박스로 채운다.

    labeled는 원본이 라벨한 CLASSES 번호 집합이다. 그 클래스의 예측은 원본 정답을 쓰므로 버린다.
    없는 클래스에 애매한 박스가 있으면 그 객체를 배경으로 배우지 않도록 이미지 전체를 제외한다.
    """
    lines = []
    for box in detections:
        name, score, coords = box['label'], box['confidence'], box['xywh']
        if name not in CLASSES:
            return {'include': False, 'reason': '알 수 없는 객체', 'labels': []}
        if CLASSES.index(name) in labeled:
            continue
        if not math.isfinite(score) or score < threshold:
            return {'include': False, 'reason': '저신뢰 객체', 'labels': []}
        if len(coords) != 4 or not all(math.isfinite(v) for v in coords) or not _inside(coords):
            return {'include': False, 'reason': '박스 좌표 오류', 'labels': []}
        lines.append(f'{CLASSES.index(name)} ' + ' '.join(f'{v:.8f}' for v in coords))
    return {'include': True, 'reason': '빠진 클래스 채움', 'labels': lines}


def align_hard_hat(boxes, detections):
    """외부 데이터셋 안전모 박스를 기준 자료 규칙(안전모만 감싼 박스)에 맞춘다.

    로보플로우 hard-hat 데이터셋은 얼굴까지 머리 전체를 감싸 박스 높이가 기준 모델 박스의 약 두 배다.
    boxes는 (CLASSES 번호, xywh) 목록이다. 원본 안전모 박스 안에 중심이 있는 기존 모델 안전모 박스로 좌표를 바꾸고,
    대응 박스가 없으면 None을 돌려 이미지를 제외하게 한다.
    """
    hat = CLASSES.index('hard_hat')
    candidates = sorted((d for d in detections if d['label'] == 'hard_hat'), key=lambda d: -d['confidence'])
    used, aligned = set(), []
    for target, coords in boxes:
        if target != hat:
            aligned.append((target, coords))
            continue
        x, y, width, height = coords
        match = next((i for i, d in enumerate(candidates) if i not in used
                      and abs(d['xywh'][0] - x) <= width / 2 and abs(d['xywh'][1] - y) <= height / 2), None)
        if match is None or not _inside(candidates[match]['xywh']):
            return None
        used.add(match)
        aligned.append((hat, candidates[match]['xywh']))
    return aligned


def select(tags, detections, threshold=0.6):
    """저신뢰 박스를 버린 뒤 남은 객체만 배경으로 오인하지 않도록 이미지 전체를 제외한다."""
    if not isinstance(tags, list) or any(tag not in CLASSES for tag in tags):
        return {'include': False, 'reason': '태그 형식 불일치', 'labels': []}
    if not tags or not detections:
        return {'include': False, 'reason': '객체 근거 부족', 'labels': []}
    lines, detected = [], set()
    for box in detections:
        name, score, coords = box['label'], box['confidence'], box['xywh']
        if name not in CLASSES or not math.isfinite(score) or score < threshold:
            return {'include': False, 'reason': '저신뢰 또는 알 수 없는 객체', 'labels': []}
        if len(coords) != 4 or not all(math.isfinite(v) for v in coords):
            return {'include': False, 'reason': '박스 좌표 오류', 'labels': []}
        if not _inside(coords):
            return {'include': False, 'reason': '이미지 범위를 벗어난 박스', 'labels': []}
        detected.add(name)
        lines.append(f'{CLASSES.index(name)} ' + ' '.join(f'{v:.8f}' for v in coords))
    if detected != set(tags):
        return {'include': False, 'reason': 'YOLO와 Gemma 클래스 불일치', 'labels': []}
    return {'include': True, 'reason': '클래스 일치·신뢰도 통과', 'labels': lines}


def generate(run, base_model, gemma, sample_ids, threshold=0.6, device='cpu'):
    from ultralytics import YOLO
    model = YOLO(str(base_model))
    if not compatible(model.names[i] for i in range(len(model.names))):
        raise ValueError('기준 모델 클래스 표가 일치하지 않습니다.')
    items = []
    for sample_id in sample_ids:
        image = run / 'central-images' / f'{sample_id}.jpg'
        result = model.predict(str(image), conf=0.25, imgsz=640, device=device, verbose=False)[0]
        detections = [{'label': model.names[int(box.cls.item())], 'confidence': float(box.conf.item()),
                       'xywh': box.xywhn[0].tolist()} for box in result.boxes]
        tags = gemma['items'][sample_id]['result']['labels']
        items.append({'sampleId': sample_id, 'sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
                      'tags': tags, 'detections': detections, **select(tags, detections, threshold)})
    return {'policy': POLICY, 'threshold': threshold, 'modelSha256': hashlib.sha256(Path(base_model).read_bytes()).hexdigest(),
            'humanReviewed': False, 'items': items}
