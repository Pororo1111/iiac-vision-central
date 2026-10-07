"""Gemma 오탐 확인(fp_check)용 기준 이미지 세트를 만든다.

사람(또는 Claude)이 확인한 데이터셋 라벨에서 보호구 정탐·오탐 예시를 잘라 .artifacts/ai-assets/fp-references/에 둔다.
Gemma는 글 설명만으로는 현장 유니폼(공항 남색 작업 조끼)을 알아보지 못하므로(2026-10-07 검증), 판정마다 이 이미지를 함께 본다.
예시는 클래스별 3~4장으로 적게 유지한다(이미지가 늘면 판정 시간이 늘어난다). 같은 입력이면 같은 결과가 나온다.
실행: .venv/bin/python scripts/build-fp-references.py
"""
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai-worker'))
from fp_check import crop_region  # noqa: E402

OUT = ROOT / '.artifacts/ai-assets/fp-references'
WEBCAM = ROOT / '.artifacts/claude-labels-20261007'
TANK = ROOT / '.artifacts/claude-labels-20261007-tank'


def webcam_box(key, cls):
    """웹캠 검토 작업 파일(scan.json)의 현장 모델 박스 중 가장 신뢰도가 높은 것(픽셀 좌표)."""
    scan = json.loads((WEBCAM / 'scan.json').read_text())
    dets = sorted((d for d in scan[key]['dets'] if d['c'] == cls), key=lambda d: -d['p'])
    return WEBCAM / 'frames' / (key + '.png'), dets[0]['xyxy']


def airport_box(box):
    """테스트 영상(공항 장면) 원본 프레임 한 장. 박스는 정규화 좌표이며 2026-10-07 Claude가 이미지로 직접 확인했다."""
    path = ROOT / '.artifacts/local-dev/media/detection-snapshots/8a39db3c-046d-5bd8-9eaa-3cb223187f98/1791252756636000.jpg'
    w, h = Image.open(path).size
    return path, [box[0] * w, box[1] * h, box[2] * w, box[3] * h]


def tank_box(index):
    scan = json.loads((TANK / 'scan.json').read_text())
    key = json.loads((TANK / 'keys.json').read_text())[index]
    dets = sorted((d for d in scan[key]['dets'] if d['c'] == 'person'), key=lambda d: -(d['xyxy'][2] - d['xyxy'][0]) * (d['xyxy'][3] - d['xyxy'][1]))
    x1, y1, x2, y2 = dets[0]['xyxy']
    # 나시 예시는 몸통 부분(사람 박스 위 20%~70%)을 조끼 박스처럼 쓴다.
    h = y2 - y1
    return ROOT / scan[key]['img'], [x1, y1 + 0.2 * h, x2, y1 + 0.7 * h]


# (파일 이름, 클래스, 판정, 설명, 원본·픽셀 박스를 돌려주는 함수). 판정은 사용자 확인 규칙(2026-10-07)을 따른다.
EXAMPLES = [
    ('vest_real_navy_airport', 'safety_vest', 'real', '공항 작업자의 남색 작업 조끼(반사띠 없음, 긴소매 위에 덧입음) → 안전조끼 맞음',
     lambda: webcam_box('sample_a32522b0', 'safety_vest')),
    ('vest_real_navy_reflective', 'safety_vest', 'real', '남색 작업복에 형광 반사띠 → 안전조끼 맞음',
     lambda: airport_box([0.725, 0.245, 0.835, 0.401])),
    ('vest_real_yellow_stripes', 'safety_vest', 'real', '남색 작업복에 노란 반사띠(앉은 자세) → 안전조끼 맞음',
     lambda: airport_box([0.318, 0.356, 0.406, 0.485])),
    ('hat_real_white', 'hard_hat', 'real', '흰색 산업용 안전모 → 안전모 맞음', lambda: airport_box([0.658, 0.181, 0.731, 0.285])),
    # 2026-10-07: 이 프레임의 [0.353, 0.297, 0.409, 0.385]는 Gemma가 '머리카락'으로 오판했지만 실제로 흰 안전모다(원본 확대 확인).
    ('hat_real_white_kneeling', 'hard_hat', 'real', '흰색 산업용 안전모(앉은 작업자) → 안전모 맞음',
     lambda: airport_box([0.353, 0.297, 0.409, 0.385])),
    ('vest_false_beige_shirt', 'safety_vest', 'false', '베이지 반팔 셔츠(와이셔츠) → 안전조끼 아님',
     lambda: webcam_box('sample_fd962874', 'safety_vest')),
    ('vest_false_tank_top', 'safety_vest', 'false', '일반 나시(민소매 티셔츠) → 안전조끼 아님', lambda: tank_box(33)),
    ('hat_false_bald', 'hard_hat', 'false', '대머리 → 안전모 아님', lambda: webcam_box('sample_13bf8f2f', 'hard_hat')),
    ('hat_false_bald_far', 'hard_hat', 'false', '멀리서 본 대머리 → 안전모 아님', lambda: webcam_box('2026-10-07_18-26-23-253120_0065', 'hard_hat')),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, cls, verdict, note, source in EXAMPLES:
        path, (x1, y1, x2, y2) = source()
        image = Image.open(path).convert('RGB')
        w, h = image.size
        crop = image.crop(crop_region(cls, [x1 / w, y1 / h, x2 / w, y2 / h], w, h))
        scale = 384 / max(crop.size)
        crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.LANCZOS)
        crop.save(OUT / (name + '.jpg'), quality=90)
        manifest.append({'file': name + '.jpg', 'cls': cls, 'verdict': verdict, 'note': note, 'source': str(path.relative_to(ROOT))})
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(len(manifest), '장 →', OUT)


if __name__ == '__main__':
    main()
