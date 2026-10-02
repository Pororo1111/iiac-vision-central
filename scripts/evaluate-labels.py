"""사람 라벨 기준 test 분할로 YOLO+Gemma 합의 자동 라벨의 품질을 측정한다.

실행: .venv/bin/python scripts/evaluate-labels.py --device mps
결과는 Git 제외 `.artifacts/label-quality/<실행명>/report.json`에 저장한다. Gemma 결과는
같은 실행 디렉터리에 캐시하므로 중단 후 같은 --run으로 이어서 실행할 수 있다.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'ai-worker')]
from pseudo_labels import CLASSES, select  # noqa: E402
from ai_assets import BASE_MODEL, BASELINE  # noqa: E402

DATASET = BASELINE.parent


def iou(a, b):
    ax1, ay1, ax2, ay2 = a[0]-a[2]/2, a[1]-a[3]/2, a[0]+a[2]/2, a[1]+a[3]/2
    bx1, by1, bx2, by2 = b[0]-b[2]/2, b[1]-b[3]/2, b[0]+b[2]/2, b[1]+b[3]/2
    inter = max(0, min(ax2, bx2)-max(ax1, bx1)) * max(0, min(ay2, by2)-max(ay1, by1))
    union = a[2]*a[3] + b[2]*b[3] - inter
    return inter / union if union > 0 else 0


def parse(lines):
    boxes = []
    for line in lines:
        parts = line.split()
        if len(parts) == 5:
            boxes.append((int(parts[0]), [float(v) for v in parts[1:]]))
    return boxes


def match(pred, truth, threshold=0.5):
    """클래스별 탐욕 매칭으로 TP/FP/FN을 센다."""
    stats = {name: {'tp': 0, 'fp': 0, 'fn': 0} for name in CLASSES}
    used = set()
    for cls, box in pred:
        best, best_iou = None, threshold
        for index, (tcls, tbox) in enumerate(truth):
            if index in used or tcls != cls:
                continue
            value = iou(box, tbox)
            if value >= best_iou:
                best, best_iou = index, value
        if best is None:
            stats[CLASSES[cls]]['fp'] += 1
        else:
            used.add(best)
            stats[CLASSES[cls]]['tp'] += 1
    for index, (tcls, _) in enumerate(truth):
        if index not in used:
            stats[CLASSES[tcls]]['fn'] += 1
    return stats


def summary(stats):
    out = {}
    for name, s in stats.items():
        p = s['tp'] / (s['tp']+s['fp']) if s['tp']+s['fp'] else None
        r = s['tp'] / (s['tp']+s['fn']) if s['tp']+s['fn'] else None
        out[name] = {**s, 'precision': p, 'recall': r}
    return out


def add(total, part):
    for name in CLASSES:
        for key in ('tp', 'fp', 'fn'):
            total[name][key] += part[name][key]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', default='labels-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    parser.add_argument('--split', default='test')
    parser.add_argument('--device', default='mps')
    parser.add_argument('--threshold', type=float, default=0.6)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()

    from ultralytics import YOLO
    import worker  # .env의 워커 설정만 읽으며 중앙 API는 호출하지 않는다.

    run = ROOT / '.artifacts/label-quality' / args.run
    run.mkdir(parents=True, exist_ok=True)
    cache_path = run / 'gemma.json'
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    model = YOLO(str(BASE_MODEL))
    images = sorted((DATASET / 'images' / args.split).glob('*.jpg'))[:args.limit]
    empty = lambda: {name: {'tp': 0, 'fp': 0, 'fn': 0} for name in CLASSES}  # noqa: E731
    selected_stats, yolo_stats = empty(), empty()
    tag = {name: {'tp': 0, 'fp': 0, 'fn': 0} for name in CLASSES}
    items, reasons = [], {}
    for index, path in enumerate(images, 1):
        label_path = DATASET / 'labels' / args.split / (path.stem + '.txt')
        truth = parse(label_path.read_text().splitlines() if label_path.exists() else [])
        truth_classes = {CLASSES[c] for c, _ in truth}
        if path.name not in cache:
            cache[path.name] = worker.automatic_analysis('gemma4:12b', Image.open(path).convert('RGB'))['labels']
            cache_path.write_text(json.dumps(cache, ensure_ascii=False))
        tags = cache[path.name]
        for name in CLASSES:
            key = 'tp' if name in tags and name in truth_classes else 'fp' if name in tags else 'fn' if name in truth_classes else None
            if key:
                tag[name][key] += 1
        result = model.predict(str(path), conf=0.25, imgsz=640, device=args.device, verbose=False)[0]
        detections = [{'label': model.names[int(b.cls.item())], 'confidence': float(b.conf.item()),
                       'xywh': b.xywhn[0].tolist()} for b in result.boxes]
        yolo_pred = [(CLASSES.index(d['label']), d['xywh']) for d in detections if d['confidence'] >= args.threshold]
        add(yolo_stats, match(yolo_pred, truth))
        decision = select(sorted(set(tags)), detections, args.threshold)
        reasons[decision['reason']] = reasons.get(decision['reason'], 0) + 1
        if decision['include']:
            add(selected_stats, match(parse(decision['labels']), truth))
        items.append({'image': path.name, 'truth': sorted(truth_classes), 'gemma': tags,
                      'yolo': sorted({d['label'] for d in detections}), 'include': decision['include'],
                      'reason': decision['reason']})
        print(f'{index}/{len(images)} {path.name} include={decision["include"]} {decision["reason"]}', flush=True)

    included = sum(item['include'] for item in items)
    report = {
        'createdAt': datetime.now().astimezone().isoformat(), 'dataset': str(DATASET), 'split': args.split,
        'baseModel': str(BASE_MODEL), 'threshold': args.threshold, 'iou': 0.5, 'images': len(items),
        'included': included, 'reasons': reasons,
        'consensusBoxes': summary(selected_stats),
        'yoloOnlyBoxesAllImages': summary(yolo_stats),
        'gemmaImageTags': summary(tag),
        'note': '사람 라벨 기준 분할에서 측정. 자동 라벨은 humanReviewed=false이며 현장 성능을 보증하지 않는다.',
        'items': items,
    }
    (run / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != 'items'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
