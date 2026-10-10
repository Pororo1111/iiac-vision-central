"""오탐 판단 기억(V37)에 Claude 판별 기본 사례를 넣는다.

10/07·10/08 iot-jetson-001 / usb-0 웹캠 사진을 Claude가 직접 판별한 작업 파일(.artifacts/claude-labels-2026100{7,8}/)에서,
당시 현장 모델이 hard_hat·safety_vest로 잡은 박스(scan.json)를 Claude 정답 박스(final.json)와 IoU 0.3으로 비교해
정탐(real)·오탐(false)을 정하고, 확대 사진의 CLIP 임베딩을 중앙에 저장한다. 같은 원본(origin)은 갱신만 한다.
결과(2026-10-09): 88건 = 안전모 오탐(대머리) 13, 조끼 오탐(베이지 반팔 셔츠) 52, 조끼 정탐(남색 작업 조끼) 23.

실행: .venv/bin/python scripts/seed-fp-memory.py [--dry-run] (CENTRAL_INTERNAL_URL로 대상 중앙 변경)
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai-worker'))
import fp_memory  # noqa: E402
from fp_check import crop_region  # noqa: E402
from worker import api  # noqa: E402

PPE = ('hard_hat', 'safety_vest')
NOTE = {('hard_hat', 'false'): '대머리(안전모 아님)', ('safety_vest', 'false'): '베이지 반팔 셔츠(조끼 아님)',
        ('safety_vest', 'real'): '남색 작업 조끼'}


def iou(a, b):
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter + 1e-9)


def cases():
    for day in ('20261007', '20261008'):
        work = ROOT / '.artifacts' / f'claude-labels-{day}'
        final = json.loads((work / 'final.json').read_text())
        scan = json.loads((work / 'scan.json').read_text())
        for key, truth in final.items():
            item = scan[key]
            if 'src' in item:
                path = ROOT / item['src']
            elif 'sample' in item:
                path = ROOT / '.artifacts/local-dev/media' / f"{item['sample']}.jpg"
            else:
                path = work / 'frames' / f'{key}.png'
            for n, det in enumerate(item['dets']):
                if det['c'] not in PPE:
                    continue
                label = 'real' if any(c == det['c'] and iou(det['xyxy'], b) >= 0.3 for c, b in truth) else 'false'
                yield {'origin': f'claude-{day}/{key}#{n}', 'path': path, 'cls': det['c'], 'label': label, 'xyxy': det['xyxy']}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true', help='중앙에 보내지 않고 건수만 센다')
    args = parser.parse_args()
    count = Counter()
    for case in cases():
        count[(case['cls'], case['label'])] += 1
        if args.dry_run:
            continue
        image = Image.open(case['path']).convert('RGB')
        w, h = image.size
        box = [case['xyxy'][0] / w, case['xyxy'][1] / h, case['xyxy'][2] / w, case['xyxy'][3] / h]
        crop = image.crop(crop_region(case['cls'], box, w, h))
        api('/fp-memory/seed', {'origin': case['origin'], 'cls': case['cls'], 'label': case['label'], 'box': box,
                                'note': NOTE.get((case['cls'], case['label'])), 'vector': fp_memory.embed(crop),
                                'model': fp_memory.NAME, 'crop': fp_memory.jpeg(crop)})
    print(('건수 확인만: ' if args.dry_run else '저장: ') + ', '.join(f'{c} {l} {n}' for (c, l), n in sorted(count.items())))


if __name__ == '__main__':
    main()
